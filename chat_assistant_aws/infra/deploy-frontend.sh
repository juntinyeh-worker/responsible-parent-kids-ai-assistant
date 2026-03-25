#!/usr/bin/env bash
#
# Deploy CloudFront + S3 + Cognito stack, upload frontend, lock ALB to CloudFront only.
# Does NOT touch ECS or rebuild Docker.
#
# Usage:
#   ./infra/deploy-frontend.sh --ecs-stack vca-0323 --admin-email user@example.com
#
set -euo pipefail

REGION="${AWS_REGION:-ap-east-2}"
ECS_STACK=""
CF_STACK=""
ADMIN_EMAIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)       REGION="$2"; shift 2 ;;
    --ecs-stack)    ECS_STACK="$2"; shift 2 ;;
    --admin-email)  ADMIN_EMAIL="$2"; shift 2 ;;
    --cf-stack)     CF_STACK="$2"; shift 2 ;;
    *)              echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$ECS_STACK" || -z "$ADMIN_EMAIL" ]]; then
  echo "Usage: $0 --ecs-stack <name> --admin-email <email> [--region <region>]"
  exit 1
fi

CF_STACK="${CF_STACK:-${ECS_STACK}-cf}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
echo "🔑 Account: $ACCOUNT_ID  Region: $REGION"

# ── Get ALB DNS from ECS stack ───────────────────────────────────────
echo "🔍 Getting ALB info from stack: $ECS_STACK"
ALB_DNS=$(aws cloudformation describe-stacks --stack-name "$ECS_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ALBDnsName'].OutputValue" --output text)
echo "   ALB: $ALB_DNS"

# ── Deploy CloudFront stack ───────────────────────────────────────────
echo "🚀 Deploying CloudFront stack: $CF_STACK"
aws cloudformation deploy \
  --template-file "$SCRIPT_DIR/cloudfront-stack.yaml" \
  --stack-name "$CF_STACK" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --parameter-overrides \
    ALBDnsName="$ALB_DNS" \
    AdminEmail="$ADMIN_EMAIL" \
  --no-fail-on-empty-changeset

# ── Get outputs ──────────────────────────────────────────────────────
echo "⏳ Reading stack outputs..."
get_output() {
  aws cloudformation describe-stacks --stack-name "$CF_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CF_DOMAIN=$(get_output CloudFrontDomainName)
CF_DIST_ID=$(get_output CloudFrontDistributionId)
STATIC_BUCKET=$(get_output StaticBucketName)
COGNITO_LOGIN_URL=$(get_output CognitoLoginUrl)
ALB_SECRET=$(get_output ALBOriginSecret)
USER_POOL_ID=$(get_output UserPoolId)

# ── Get Cognito config from ECS stack outputs ────────────────────────
get_ecs_output() {
  aws cloudformation describe-stacks --stack-name "$ECS_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
COGNITO_CLIENT_ID=$(get_ecs_output UserPoolClientId)
COGNITO_REGION=$(get_ecs_output CognitoRegion)

echo "   CloudFront: $CF_DOMAIN"
echo "   S3 Bucket:  $STATIC_BUCKET"
echo "   Cognito Client ID: $COGNITO_CLIENT_ID"
echo "   Cognito Region: $COGNITO_REGION"

# ── Update CloudFront Function with actual Cognito URL ───────────────
echo "🔧 Updating auth function with Cognito login URL..."
FUNC_NAME="${CF_STACK}-auth"
FUNC_ETAG=$(aws cloudfront describe-function --name "$FUNC_NAME" --query "ETag" --output text 2>/dev/null || echo "")

if [[ -n "$FUNC_ETAG" && "$FUNC_ETAG" != "None" ]]; then
  cat > /tmp/cf-auth-func.js <<FUNCEOF
function handler(event) {
  var request = event.request;
  var cookies = parseCookies(request.headers.cookie);
  if (request.uri.startsWith('/api/') || request.uri.startsWith('/auth/')) {
    return request;
  }
  if (cookies['cvt-session']) { return request; }
  return {
    statusCode: 302,
    statusDescription: 'Found',
    headers: { location: { value: '${COGNITO_LOGIN_URL}' } }
  };
}
function parseCookies(h) {
  var c = {};
  if (!h || !h.value) return c;
  h.value.split(';').forEach(function(s) {
    var p = s.trim().split('=');
    if (p.length >= 2) c[p[0]] = p.slice(1).join('=');
  });
  return c;
}
FUNCEOF

  aws cloudfront update-function \
    --name "$FUNC_NAME" \
    --if-match "$FUNC_ETAG" \
    --function-config "Comment=Cognito auth redirect,Runtime=cloudfront-js-2.0" \
    --function-code "fileb:///tmp/cf-auth-func.js" > /dev/null 2>&1

  NEW_ETAG=$(aws cloudfront describe-function --name "$FUNC_NAME" --query "ETag" --output text)
  aws cloudfront publish-function --name "$FUNC_NAME" --if-match "$NEW_ETAG" > /dev/null 2>&1
  echo "   ✅ Auth function updated"
fi

# ── Inject Cognito config into login.html ─────────────────────────────
echo "🔧 Injecting Cognito config into login.html..."
STAGING_DIR=$(mktemp -d)
cp -r "$PROJECT_DIR/static/"* "$STAGING_DIR/"

sed -i.bak \
  -e "s|{{COGNITO_REGION}}|${COGNITO_REGION}|g" \
  -e "s|{{COGNITO_CLIENT_ID}}|${COGNITO_CLIENT_ID}|g" \
  -e "s|{{LOGIN_EMAIL}}|${ADMIN_EMAIL}|g" \
  "$STAGING_DIR/login.html"
rm -f "$STAGING_DIR/login.html.bak"
echo "   ✅ Cognito config injected"

# ── Upload frontend to S3 ────────────────────────────────────────────
echo "📤 Uploading frontend to S3..."
aws s3 sync "$STAGING_DIR/" "s3://$STATIC_BUCKET/" \
  --delete --cache-control "public, max-age=3600" --region "$REGION"

aws s3 cp "$STAGING_DIR/index.html" "s3://$STATIC_BUCKET/index.html" \
  --cache-control "no-cache, no-store, must-revalidate" \
  --content-type "text/html" --region "$REGION"

rm -rf "$STAGING_DIR"
echo "   ✅ Frontend uploaded"

# ── Invalidate CloudFront cache ──────────────────────────────────────
echo "🔄 Invalidating CloudFront cache..."
aws cloudfront create-invalidation --distribution-id "$CF_DIST_ID" --paths "/*" > /dev/null 2>&1

# ── Lock ALB: update listener to require secret header ───────────────
echo "🔒 Locking ALB to CloudFront only..."

# Find the ALB ARN and listener ARN from the ECS stack
ALB_ARN=$(aws elbv2 describe-load-balancers --names "${ECS_STACK}-alb" --region "$REGION" \
  --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "")

if [[ -n "$ALB_ARN" && "$ALB_ARN" != "None" ]]; then
  LISTENER_ARN=$(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" --region "$REGION" \
    --query "Listeners[0].ListenerArn" --output text)

  TG_ARN=$(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" --region "$REGION" \
    --query "Listeners[0].DefaultActions[0].TargetGroupArn" --output text)

  # Change default action to return 403 (block direct access)
  aws elbv2 modify-listener --listener-arn "$LISTENER_ARN" --region "$REGION" \
    --default-actions '[{"Type":"fixed-response","FixedResponseConfig":{"StatusCode":"403","ContentType":"text/plain","MessageBody":"Forbidden - use CloudFront"}}]' > /dev/null 2>&1

  # Add rule: if X-CF-Origin-Verify header matches, forward to target group
  # First check if rule already exists
  EXISTING_RULES=$(aws elbv2 describe-rules --listener-arn "$LISTENER_ARN" --region "$REGION" \
    --query "Rules[?Priority!='default'].Priority" --output text)

  if [[ -z "$EXISTING_RULES" ]]; then
    aws elbv2 create-rule --listener-arn "$LISTENER_ARN" --region "$REGION" \
      --priority 1 \
      --conditions '[{"Field":"http-header","HttpHeaderConfig":{"HttpHeaderName":"X-CF-Origin-Verify","Values":["'"$ALB_SECRET"'"]}}]' \
      --actions '[{"Type":"forward","TargetGroupArn":"'"$TG_ARN"'"}]' > /dev/null 2>&1
    echo "   ✅ ALB locked — only CloudFront can reach it"
  else
    echo "   ⏭️  ALB rule already exists, skipping"
  fi
else
  echo "   ⚠️  Could not find ALB, skipping lock"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Deployed!"
echo "  🌐 URL: $CF_DOMAIN"
echo "  🔐 Login: Cognito hosted UI (auto-redirect)"
echo "  📋 Health: $CF_DOMAIN/api/health"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  Temp password sent to: $ADMIN_EMAIL"
echo ""
echo "  Add users:"
echo "  aws cognito-idp admin-create-user --user-pool-id $USER_POOL_ID --username <email> --user-attributes Name=email,Value=<email> --region $REGION"
echo ""
echo "  Tear down:"
echo "  aws s3 rm s3://$STATIC_BUCKET --recursive --region $REGION"
echo "  aws cloudformation delete-stack --stack-name $CF_STACK --region $REGION"
