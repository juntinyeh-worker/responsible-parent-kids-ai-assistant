#!/usr/bin/env bash
#
# Upload frontend to S3, inject Cognito config, invalidate CloudFront cache.
# Reads all outputs from the single ECS stack (no separate CF stack needed).
#
# Usage:
#   ./infra/deploy-frontend.sh --stack child-voice-tutor --admin-email user@example.com
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
STACK_NAME=""
ADMIN_EMAIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)       REGION="$2"; shift 2 ;;
    --stack)        STACK_NAME="$2"; shift 2 ;;
    --admin-email)  ADMIN_EMAIL="$2"; shift 2 ;;
    *)              echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$STACK_NAME" || -z "$ADMIN_EMAIL" ]]; then
  echo "Usage: $0 --stack <name> --admin-email <email> [--region <region>]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
echo "🔑 Account: $ACCOUNT_ID  Region: $REGION"

# ── Get stack outputs ────────────────────────────────────────────────
echo "⏳ Reading stack outputs from: $STACK_NAME"
get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CF_URL=$(get_output CloudFrontURL)
CF_DIST_ID=$(get_output DistributionId)
STATIC_BUCKET=$(get_output StaticBucket)
USER_POOL_ID=$(get_output UserPoolId)
COGNITO_CLIENT_ID=$(get_output UserPoolClientId)
COGNITO_REGION=$(get_output CognitoRegion)

echo "   CloudFront: $CF_URL"
echo "   S3 Bucket:  $STATIC_BUCKET"
echo "   Cognito Client ID: $COGNITO_CLIENT_ID"
echo "   Cognito Region: $COGNITO_REGION"

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

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Frontend deployed!"
echo "  🌐 URL: $CF_URL"
echo "  📋 Health: $CF_URL/api/health"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  Add users:"
echo "  aws cognito-idp admin-create-user --user-pool-id $USER_POOL_ID --username <email> --user-attributes Name=email,Value=<email> --region $REGION"
echo ""
echo "  Tear down:"
echo "  aws s3 rm s3://$STATIC_BUCKET --recursive --region $REGION"
echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION"
