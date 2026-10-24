#!/usr/bin/env bash
# One-command deploy:  scripts/deploy.sh staging   |   scripts/deploy.sh production
# Builds both images tagged with the git sha, pushes to ECR, applies Terraform (which rolls the ECS
# services; the ECS deployment circuit breaker rolls back automatically if new tasks fail health checks),
# then waits for the services to be stable and smoke-tests /healthz.
set -euo pipefail

ENV="${1:?usage: deploy.sh staging|production}"
cd "$(dirname "$0")/.."
TF=infra/terraform
SHA="$(git rev-parse --short=12 HEAD)"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing to deploy a dirty working tree" >&2; exit 1
fi
if [[ "$ENV" == "production" ]]; then
  git fetch -q origin main
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || { echo "production deploys only from origin/main" >&2; exit 1; }
  read -r -p "Deploy $SHA to PRODUCTION? type 'yes': " ok; [[ "$ok" == "yes" ]] || exit 1
fi

terraform -chdir=$TF init -input=false -reconfigure -backend-config="envs/$ENV.backend.hcl" >/dev/null
API_REPO="$(terraform -chdir=$TF output -raw ecr_api)"
DASH_REPO="$(terraform -chdir=$TF output -raw ecr_dashboard)"
API_URL="$(terraform -chdir=$TF output -raw api_url)"
REGISTRY="${API_REPO%%/*}"

aws ecr get-login-password --region eu-west-2 | docker login --username AWS --password-stdin "$REGISTRY"
docker build -f infra/docker/api.Dockerfile -t "$API_REPO:$SHA" .
docker build -f infra/docker/dashboard.Dockerfile --build-arg NEXT_PUBLIC_API_URL="$API_URL" -t "$DASH_REPO:$SHA" .
docker push "$API_REPO:$SHA"
docker push "$DASH_REPO:$SHA"

terraform -chdir=$TF apply -input=false -var-file="envs/$ENV.tfvars" -var="image_tag=$SHA" -auto-approve

CLUSTER="callbridge-$ENV"
aws ecs wait services-stable --region eu-west-2 --cluster "$CLUSTER" --services api dashboard
curl -fsS "$API_URL/healthz" >/dev/null && echo "deployed $SHA to $ENV: healthy"
