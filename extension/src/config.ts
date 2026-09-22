// Build-time settings, written to extension/.env.local by infra/login_setup.sh.
// None are secret, but the runtime ARN carries the AWS account ID, so the file
// stays out of the public repo.

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`${name} is not set. Run infra/login_setup.sh, then rebuild the extension.`);
  }
  return value;
}

export const config = {
  region: required("VITE_REGION", import.meta.env.VITE_REGION),
  runtimeArn: required("VITE_RUNTIME_ARN", import.meta.env.VITE_RUNTIME_ARN),
  clientId: required("VITE_CLIENT_ID", import.meta.env.VITE_CLIENT_ID),
  loginHost: required("VITE_LOGIN_HOST", import.meta.env.VITE_LOGIN_HOST),
};

export function invocationUrl(): string {
  const arn = encodeURIComponent(config.runtimeArn);
  return `https://bedrock-agentcore.${config.region}.amazonaws.com/runtimes/${arn}/invocations?qualifier=DEFAULT`;
}
