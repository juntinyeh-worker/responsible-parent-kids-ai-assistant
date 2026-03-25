#!/usr/bin/env bash
#
# Build, push to ECR, and deploy/update the ECS stack via CloudFormation.
#
# Usage:
#   ./infra/deploy.sh                          # Uses defaults
#   ./infra/deploy.sh --region us-west-2       # Override region
#   ./infra/deploy.sh --skip-build             # Redeploy without rebuilding image
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate credentials
#   - Docker running
#   - jq installed (brew install jq)
#
# Required env vars (or set in .env):
#   OPENAI_API_KEY  — passed to ECS task definition
#
# Optional env vars:
#   AWS_REGION      — default: us-east-1
#   VPC_ID          — auto-detected if not set
#   SUBNET_IDS      — auto-detected if not set (public subnets)
#   S3_LOG_BUCKET   — optional, for conversation log shipping
#   STACK_NAME      — default: child-voice-tutor
#   ECR_REPO_NAME   — default: child-voice-tutor

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-child-voice-tutor}"
ECR_REPO="${ECR_REPO_NAME:-child-voice-tutor}"
S3_BUCKET="${S3_LOG_BUCKET:-}"
SKIP_BUILD=false

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)      REGION="$2"; shift 2 ;;
    --skip-build)  SKIP_BUILD=true; shift ;;
    --stack)       STACK_NAME="$2"; shift 2 ;;
    *)             echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Load .env if present ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  echo "📄 Loading .env"
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

# ── Validate ──────────────────────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
echo "🔑 AWS Account: $ACCOUNT_ID  Region: $REGION"

# ── Auto-detect VPC and subnets if not provided ──────────────────────
if [[ -z "${VPC_ID:-}" ]]; then
  echo "🔍 Auto-detecting default VPC..."
  VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=isDefault,Values=true" \
    --query "Vpcs[0].VpcId" --output text --region "$REGION")
  if [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]]; then
    echo "❌ No default VPC found. Set VPC_ID env var."
    exit 1
  fi
  echo "   VPC: $VPC_ID"
fi

if [[ -z "${SUBNET_IDS:-}" ]]; then
  echo "🔍 Auto-detecting public subnets..."
  SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=map-public-ip-on-launch,Values=true" \
    --query "Subnets[*].SubnetId" --output text --region "$REGION" | tr '\t' ',')
  if [[ -z "$SUBNET_IDS" ]]; then
    # Fallback: just grab the first 2 subnets in the VPC
    SUBNET_IDS=$(aws ec2 describe-subnets \
      --filters "Name=vpc-id,Values=$VPC_ID" \
      --query "Subnets[0:2].SubnetId" --output text --region "$REGION" | tr '\t' ',')
  fi
  echo "   Subnets: $SUBNET_IDS"
fi

# ── Create ECR repo if needed ────────────────────────────────────────
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"

if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" &>/dev/null; then
  echo "📦 Creating ECR repository: $ECR_REPO"
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" --output text > /dev/null
fi

# ── Build & push Docker image ────────────────────────────────────────
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_URI="$ECR_URI:$IMAGE_TAG"
IMAGE_LATEST="$ECR_URI:latest"

if [[ "$SKIP_BUILD" == false ]]; then
  echo "🐳 Logging into ECR..."
  aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

  echo "🔨 Building Docker image (linux/amd64)..."
  docker build --platform linux/amd64 -t "$ECR_REPO:latest" "$PROJECT_DIR"

  echo "🏷️  Tagging: $IMAGE_URI"
  docker tag "$ECR_REPO:latest" "$IMAGE_URI"
  docker tag "$ECR_REPO:latest" "$IMAGE_LATEST"

  echo "⬆️  Pushing to ECR..."
  docker push "$IMAGE_URI"
  docker push "$IMAGE_LATEST"
  echo "✅ Image pushed: $IMAGE_URI"
else
  # Use latest tag when skipping build
  IMAGE_URI="$IMAGE_LATEST"
  echo "⏭️  Skipping build, using: $IMAGE_URI"
fi

# ── Deploy CloudFormation stack ──────────────────────────────────────
echo "🚀 Deploying CloudFormation stack: $STACK_NAME"

aws cloudformation deploy \
  --template-file "$SCRIPT_DIR/ecs-stack.yaml" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --parameter-overrides \
    VpcId="$VPC_ID" \
    SubnetIds="$SUBNET_IDS" \
    ImageUri="$IMAGE_URI" \
    SecretsName="${SECRETS_NAME:-openai-api-key}" \
    S3LogBucket="$S3_BUCKET" \
  --no-fail-on-empty-changeset

# ── Print ALB URL ────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for stack outputs..."
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ALBDnsName'].OutputValue" \
  --output text --region "$REGION")

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Deployed!"
echo "  🌐 URL: http://$ALB_DNS"
echo "  📋 Health: http://$ALB_DNS/api/health"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Useful commands:"
echo "  # Watch ECS service events"
echo "  aws ecs describe-services --cluster $STACK_NAME --services ${STACK_NAME}-svc --region $REGION --query 'services[0].events[:5]'"
echo ""
echo "  # Tail container logs"
echo "  aws logs tail /ecs/$STACK_NAME --follow --region $REGION"
echo ""
echo "  # Force new deployment (after pushing a new image)"
echo "  aws ecs update-service --cluster $STACK_NAME --service ${STACK_NAME}-svc --force-new-deployment --region $REGION"
echo ""
echo "  # Tear down"
echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION"
