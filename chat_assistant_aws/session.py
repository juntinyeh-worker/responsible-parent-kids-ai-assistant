"""Nova Sonic bidirectional streaming session via Amazon Bedrock.

Manages the lifecycle of a speech-to-speech session:
  sessionStart → promptStart → system prompt → audio streaming → sessionEnd
"""

import asyncio
import base64
import json
import logging
import uuid

from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamInputChunk,
    BidirectionalInputPayloadPart,
)
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.chain import create_default_chain
from smithy_aws_core.identity.container import ContainerCredentialsResolver
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver
from smithy_http.aio.crt import AWSCRTHTTPClient

from prompts import get_system_prompt

logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-2-sonic-v1:0"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000


class NovaSonicSession:
    """Manages a single Nova Sonic bidirectional streaming session."""

    def __init__(self, region: str = "us-east-1", voice_id: str = "tiffany"):
        self.region = region
        self.voice_id = voice_id
        self.model_id = MODEL_ID
        self.client = None
        self.stream = None
        self.is_active = False
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())

    def _initialize_client(self):
        import os
        http_client = AWSCRTHTTPClient()
        # ECS Fargate: use container credentials (Task Role)
        # Local dev: use environment variables
        if os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
            resolver = ContainerCredentialsResolver(http_client=http_client)
        else:
            resolver = EnvironmentCredentialsResolver()

        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=resolver,
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, event_json: str):
        if not self.is_active or not self.stream:
            return
        try:
            event = InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
            )
            await self.stream.input_stream.send(event)
        except Exception as e:
            logger.warning(f"send_event failed: {e}")
            self.is_active = False

    async def start(self):
        """Start the Nova Sonic session: sessionStart → promptStart → system prompt."""
        if not self.client:
            self._initialize_client()

        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.is_active = True

        # Session start
        await self.send_event(json.dumps({
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    }
                }
            }
        }))

        # Prompt start
        await self.send_event(json.dumps({
            "event": {
                "promptStart": {
                    "promptName": self.prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": self.voice_id,
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                }
            }
        }))

        # System prompt
        await self.send_event(json.dumps({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        }))

        await self.send_event(json.dumps({
            "event": {
                "textInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                    "content": get_system_prompt(),
                }
            }
        }))

        await self.send_event(json.dumps({
            "event": {
                "contentEnd": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                }
            }
        }))

        logger.info(f"Nova Sonic session started: prompt={self.prompt_name}")

    async def start_audio_input(self):
        """Signal start of user audio stream."""
        self.audio_content_name = str(uuid.uuid4())
        await self.send_event(json.dumps({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": INPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64",
                    },
                }
            }
        }))

    async def send_audio_chunk(self, audio_b64: str):
        """Send a base64-encoded audio chunk."""
        if not self.is_active:
            return
        try:
            await self.send_event(json.dumps({
                "event": {
                    "audioInput": {
                        "promptName": self.prompt_name,
                        "contentName": self.audio_content_name,
                        "content": audio_b64,
                    }
                }
            }))
        except Exception:
            pass  # Non-critical, stream may be closing

    async def end_audio_input(self):
        """Signal end of user audio stream."""
        await self.send_event(json.dumps({
            "event": {
                "contentEnd": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                }
            }
        }))

    async def process_responses(self, on_audio, on_text, on_event):
        """Read responses from the stream and dispatch to callbacks.

        Args:
            on_audio: async callback(base64_audio_str)
            on_text: async callback(role, text)
            on_event: async callback(event_dict) for other events
        """
        try:
            role = "ASSISTANT"
            while self.is_active:
                output = await self.stream.await_output()
                result = await output[1].receive()

                if not (result.value and result.value.bytes_):
                    continue

                data = json.loads(result.value.bytes_.decode("utf-8"))
                if "event" not in data:
                    continue

                evt = data["event"]

                if "contentStart" in evt:
                    cs = evt["contentStart"]
                    role = cs.get("role", role)
                    speculative = False
                    if "additionalModelFields" in cs:
                        try:
                            extra = json.loads(cs["additionalModelFields"])
                            speculative = extra.get("generationStage") == "SPECULATIVE"
                        except Exception:
                            pass
                    await on_event({"type": "contentStart", "role": role, "speculative": speculative})

                elif "textOutput" in evt:
                    text = evt["textOutput"]["content"]
                    await on_text(role, text)

                elif "audioOutput" in evt:
                    await on_audio(evt["audioOutput"]["content"])

                elif "contentEnd" in evt:
                    await on_event({"type": "contentEnd", "role": role})

        except asyncio.CancelledError:
            pass  # Normal during shutdown
        except Exception as e:
            if self.is_active:
                logger.error(f"Response processing error: {e}")

    async def end(self):
        """End the session cleanly."""
        if not self.is_active:
            return
        self.is_active = False

        try:
            await self.send_event(json.dumps({
                "event": {"promptEnd": {"promptName": self.prompt_name}}
            }))
            await self.send_event(json.dumps({
                "event": {"sessionEnd": {}}
            }))
        except Exception:
            pass  # Stream may already be closed

        try:
            await self.stream.input_stream.close()
        except Exception:
            pass  # CRT may raise on cancelled futures

        logger.info(f"Nova Sonic session ended: prompt={self.prompt_name}")
