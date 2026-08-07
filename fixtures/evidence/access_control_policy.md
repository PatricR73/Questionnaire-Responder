# Access Control Policy

## Authentication

All employee accounts require multi-factor authentication (MFA) using a hardware key
or an authenticator app. Passwords must be at least 14 characters and are rotated
every 180 days. Shared accounts are prohibited.

## Encryption

### Encryption at rest

All customer data stored in production databases is encrypted at rest using AES-256.
Encryption keys are managed via a dedicated key management service and rotated
annually.

### Encryption in transit

All network traffic between clients and production services is encrypted in transit
using TLS 1.2 or higher. Internal service-to-service traffic within the production
VPC is also encrypted using mutual TLS.

## Access Reviews

Access to production systems is reviewed quarterly by the security team. Any account
that has not been used in 90 days is automatically disabled.
