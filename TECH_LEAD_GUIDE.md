# Tech Lead Operational Guide & HackerOne Monetization Roadmap

## Part 1: Operational Protocol & Mindset

### 1. Division of Roles
- **You (Product Owner & Researcher)**:
  - Select targets based on clear criteria.
  - Set up two test accounts (User A, User B) within the authorized scope.
  - Execute controlled manual verification via Burp Suite, Caido, or curl.
  - Submit reports and communicate with program triage teams.
- **Antigravity (Tech Lead & Architect)**:
  - Break down technical features into executable 45-minute sprints.
  - Architect tools, write specifications, and review generated code.
  - Formulate safe, developer-oriented prompts to prevent AI guardrail triggers.
- **Claude Code Cloud (Implementation Engine on Opus)**:
  - Generate clean, modular production code in English without comments.
  - Run automated unit tests and push commits to GitHub.

### 2. Behavioral Rules & Hygiene
1. **Zero Analysis Paralysis**: Never spend more than 30 minutes evaluating a target. If an asset is a static brochure with no API surface, discard it immediately and move on.
2. **Anti-Filter Hygiene**: Never use offensive terminology (`exploit`, `hack`, `idor`, `bypass`, `vulnerability`) in AI prompts or commit messages. Always frame requests in developer language:
   - `OpenAPI schema reconstruction`
   - `Client-side API contract mapping`
   - `State model & parameter parity analysis`
   - `Access control verification`
3. **Strict Safe Harbor Compliance**:
   - Only touch assets explicitly listed under **In Scope**.
   - Never access, modify, or exfiltrate another user's data. Test access control exclusively between two accounts you own.
   - Do not perform Denial of Service (DoS) or automated brute force.

---

## Part 2: End-to-End HackerOne Monetization Pipeline

### Phase 1: Target Selection (The 80/20 Rule)
Do not compete against thousands of seasoned hunters on mature targets like Uber, Shopify, or GitHub. Focus on high-yield, low-competition programs:
- **Filters**:
  - `Offers Bounties == true` (exclude unpaid VDP).
  - `Asset Type == URL / Wildcard (*.domain.com)`.
  - `Response Efficiency >= 85%` (triaged within 48 hours).
  - `Average Bounty >= $300`.
- **Target Profile**:
  - Fast-growing B2B SaaS, fintech microservices, modern e-commerce.
  - Applications using modern Single Page Application (SPA) stacks: Next.js, Vite, React, Vue, Inertia.js.

### Phase 2: Automated API Reconnaissance
1. Launch `api-spec-extractor`:
   ```bash
   python main.py --url https://app.target.com --title "Target API Mapping"
   ```
2. The pipeline extracts:
   - All client-side JavaScript bundles and dynamically loaded chunks.
   - Build manifests (`/build/manifest.json`, `/.vite/manifest.json`).
   - Inline framework routing dictionaries (e.g. Laravel Ziggy, Next.js page props).
   - Reconstructed OpenAPI 3.0 specification (`output/openapi.json`).
3. Import `openapi.json` into **Postman** or **Caido/Burp Suite** to view the entire attack surface.

### Phase 3: Differential Analysis (Finding Logic Flaws)
Compare the visible user interface against the reconstructed API specification:
1. **Hidden & Administrative Endpoints**:
   - Identify routes present in JavaScript bundles that have no corresponding buttons or links in the UI (e.g., `/api/admin/*`, `/api/internal/*`, `/api/v2/export`).
2. **Mass Assignment / Parameter Tampering**:
   - Look at extracted state models and payload keys.
   - Test whether updating a user profile (`PUT /api/user/profile`) accepts unauthorized fields found in client state (e.g., `role: "admin"`, `is_verified: true`, `organization_id: 1`, `credits: 9999`).
3. **Broken Object Level Authorization (BOLA / IDOR)**:
   - On endpoints taking object identifiers (`GET /api/documents/{id}`, `POST /api/invoices/{id}/download`):
   - Authenticate as User A, request an object belonging to User B.
   - Check if the server validates resource ownership or simply returns the data.

### Phase 4: Professional Report Formulation
Submitting a sloppy report leads to delays, disputes, or downgrades. Every report must follow this exact structure:
1. **Title**: Clear vulnerability type and affected endpoint (e.g., `BOLA on /api/v1/invoices/{id} exposes customer billing records`).
2. **Severity / CVSS**: Objective CVSS 3.1 score with metric breakdown.
3. **Summary**: Concise business impact explanation.
4. **Steps to Reproduce**: Minimal, deterministic steps using curl commands with sample tokens.
5. **Remediation**: Exact developer fix (e.g., `Verify that the authenticated tenant matches document.tenant_id before query execution`).

---

## Part 3: Roadmap to Financial Autonomy (Age 17 to 18)

```
[Sprint 1-10: Tooling] ───► [Months 1-2: First Bounties] ───► [Months 3-6: Scaling] ───► [Months 6-12: Autonomy]
Build local recon hub      Submit 5 targeted reports        Specialize in SPA API logic    Private invitations
Consume $100 Opus credits  Target: $200 - $600              Target: $1,500 - $2,500/mo     Target: $4,000+/mo (Full Independence)
```

- **Phase 1 (Days 1–10, until Oct 5)**:
  - Finish building `api-spec-extractor` with Radar & Web UI using your $100 Opus 5.5 credits.
  - Have a fully operational personal reconnaissance platform ready on your desktop.
- **Phase 2 (Months 1–2: Validation)**:
  - Select 3 fresh B2B SaaS programs from the Radar.
  - Map APIs, find 1–2 confirmed business logic flaws (Mass Assignment or IDOR).
  - Earn first bounty ($200–$600) to validate the workflow and establish a HackerOne track record.
- **Phase 3 (Months 3–6: Consistency)**:
  - Establish a routine: 15 hours per week (2 hours on school evenings, weekend sprint).
  - Target 2–3 accepted reports per month ($1,500–$2,500/mo).
- **Phase 4 (Months 6–12: Autonomy by Age 18)**:
  - High HackerOne signal unlocks private program invitations (far lower competition, higher payouts).
  - Achieve full financial autonomy before reaching age 18.
