# Run this Python script to generate a random transport secret key and its corresponding public key, both encoded in base64 for use as environment variables.
import vetkd_py
import base64

# Generate a random transport secret key (32 bytes, Ed25519)
secret_key = vetkd_py.generate_transport_secret_key()

# Derive the corresponding public key
public_key = vetkd_py.transport_public_key_from_secret(secret_key)

# Encode both as base64 for environment variables
secret_b64 = base64.b64encode(secret_key).decode("ascii")
public_b64 = base64.b64encode(public_key).decode("ascii")

print(f"HAVEN_AOL_TRANSPORT_SECRET_KEY_B64={secret_b64}")
print(f"HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64={public_b64}")