# Quickstart

## Goal

Run local runtime and reach first successful response without manual model tuning.

## API

1. Check onboarding profile: `GET /models/onboarding/profile`
2. Get activation plan: `GET /models/onboarding/activation-plan`
3. Activate selected package: `POST /models/onboarding/activate`

## Verification

- Service responds on `/v1/chat/completions`
- Onboarding finishes with `activated` or `activated_with_smoke_warning`

