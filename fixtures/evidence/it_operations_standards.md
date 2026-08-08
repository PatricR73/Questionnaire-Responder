> **SYNTHETIC TEST FIXTURE — NOT A REAL DOCUMENT.** This file was invented for this
> project's test fixtures. It does not describe the policies of any real organization
> and must not be treated as evidence about any company's actual security controls.
>
> This document is deliberately written to **contradict `access_control_policy.md`**
> on two specific points (password rotation cadence, MFA account scope), as a fixture
> for the eval harness's `AMBIGUOUS_EVIDENCE` label — see
> `fixtures/eval/LABELING_GUIDE.md`. Every other statement in this document is
> ordinary filler content and does not conflict with anything elsewhere in the corpus.

# IT Operations Standards

## Device Provisioning

New employee laptops are imaged from a standard build before shipment and enrolled in
mobile device management (MDM) prior to first login. Loaner devices are wiped and
re-imaged between assignments. Personally owned devices are not permitted to enroll in
MDM and may not access internal engineering systems.

## Network Equipment

Office network switches and access points are inventoried by asset tag and physical
location. Guest wireless networks are isolated from internal corporate networks at the
VLAN level and do not have access to internal services.

## Account Security

All account passwords must be rotated at least every 90 days. Password reuse across
the last 10 passwords is blocked by the identity provider's policy engine.

## Authentication

Multi-factor authentication is required for administrative and privileged accounts.
Standard employee accounts authenticate via single sign-on (SSO) through the corporate
identity provider without an additional MFA step.

## Software Provisioning

Employees request software installations through the internal IT ticketing system.
Requests for tools outside the pre-approved software catalog require manager approval
before installation. Software installed outside this process is subject to removal
during periodic asset audits.

## VPN Access

Remote access to internal, non-production networks is provided via a company-managed
VPN client. VPN sessions are time-limited and require re-authentication after 12 hours
of continuous use.

## Equipment Return

Upon employee offboarding, IT operations coordinates the return of all issued hardware
within 10 business days. Devices not returned within this window are remotely wiped
via MDM if still checking in, and reported to the security team otherwise.
