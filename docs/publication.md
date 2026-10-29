# Publication checklist

Target: `Jhoan-JS/integration-event-inbox`. Initial visibility: **private**.

- [x] Review existing public projects and choose a distinct backend problem.
- [x] Use synthetic fixtures and an independently defined public contract.
- [x] Keep credentials, local data, caches and virtual environments out of Git.
- [x] Include automated tests, dependency lock, OpenAPI and reproducible commands.
- [x] Explain receipt semantics and limitations without production-readiness claims.
- [x] Create the new private GitHub repository.
- [x] Confirm private visibility after creation.
- [ ] Push the reviewed implementation.
- [ ] Check GitHub Actions results, including Windows.
- [ ] Owner reviews the code and can explain transactions, retries and error cases.
- [ ] Obtain the owner's explicit authorization before making the repository public.
- [ ] After publication, separately approve linking it from the GitHub profile.

The owner authorized creation and upload to the private repository on 2026-10-29.
Public visibility remains subject to a separate explicit approval.

Before using this in an interview, reproduce the 201 → 200 → 409 flow, demonstrate
that a restart preserves the receipt, and explain why a receipt does not mean
downstream success. Avoid claiming this code was deployed for a client.
