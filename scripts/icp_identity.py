# Run this Python script to generate a new ICP identity (Ed25519 key pair) and save it in PEM format for use as an environment variable.
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import os

# Generate Ed25519 private key
private_key = Ed25519PrivateKey.generate()

# Serialize to PEM format
pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Save to file
os.makedirs(r"C:\Users\User\.config\haven", exist_ok=True)
path = r"C:\Users\User\.config\haven\identity.pem"
with open(path, "wb") as f:
    f.write(pem)

print(f"ICP identity saved to: {path}")
print(f"Set: $env:HAVEN_ICP_IDENTITY_PEM_PATH = \"{path}\"")