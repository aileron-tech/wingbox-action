# Control matrix: which evidence this check can give, and where it stops

Status: DRAFT, not reviewed.

- Reviewed by: (pending)
- Review date: (pending)
- Editions written against: ISO/IEC 27001:2022 (third edition, 2022-10), Annex A, with the control
  numbers and titles of ISO/IEC 27002:2022 (third edition, 2022-02, corrected version 2022-03);
  ISO/IEC 42001:2023 (first edition, 2023-12), Annex A; the AICPA 2017 Trust Services Criteria (with
  revised points of focus, 2022), the criteria of a SOC 2 examination.
- Identifiers and titles checked on: 2026-11-19, against the sources listed at the end. No text of a
  standard or of the criteria is copied here: identifiers and titles only.

Teams ask which controls the records of this check can support. This page answers for three regimes, row
by row: why the control concerns a team that develops software, the exact evidence a run gives, what a
reviewer still needs from elsewhere, and the limit of what code evidence shows.

How to read it:

- The list is not complete. It holds only rows where the evidence of this check plausibly supports a
  part of a control: secure development and change control. A control that is not listed is not
  thereby outside the scope of a team, and a listed row is not thereby applicable to a team: that
  follows from the team's own risk assessment and scope.
- A row never says that the tool meets a control. The words are: a run *records* something, the record
  *supports evidence for* a part of the control, and it *does not show* the rest.
- The evidence column names fields as they are written: of the record (`wingbox-evidence.json`,
  `schema/wingbox-evidence-2.schema.json`), of the manifest of a package
  (`schema/wingbox-package-1.schema.json`), of a ledger line (`schema/wingbox-ledger-1.schema.json`),
  of the period report (`schema/wingbox-period-1.schema.json`) and of a review record
  (`schema/wingbox-review-1.schema.json`).
- The last column says where the identifier and the title of the row were checked, and when. It is
  there for the review of this page and can be dropped afterwards.
- [`matrix.controls.yaml`](matrix.controls.yaml) holds the same rows in the format of a control mapping
  (see "Control mapping" in the README), as a reference example. Every control in it is a `draft`, so
  it supports no claim by itself: a team copies a row into its own mapping, under its own control id,
  with its own scope, claim and approval.

## ISO/IEC 27001:2022, Annex A

Not promised: certification of a management system, and the organisational requirements, which no code
shows. What a run gives is support for evidence on secure development, change management, access to
source code and technical measures, and it counts only when it is tied to the team's own statement of
applicability and risk assessment.

The numbers and titles below are those of ISO/IEC 27002:2022, clauses 5 and 8, whose table of contents
is public [S1]. The table of Annex A is not in the public preview: that it lists the same controls
under the same numbers and titles is for the reviewer to confirm (see the end). Many teams write the
numbers with the prefix "A.".

| Control | Title | Why it concerns a software team | Evidence a run gives | Still needed from elsewhere | Limit | Source, checked |
|---|---|---|---|---|---|---|
| 5.3 | Segregation of duties | The author of a change should not be the one who decides what the change is checked by. | Record: `policy.changed`, `policy.judged-with`, `policy.self-accepted`. Package manifest: `policy`. A change that edits a rule file, the baseline, the manifest of required tests, the control mapping or the workflow is listed, and its findings are judged with those files as the base commit has them. | The code host's records of who approved the change, and of the rule that protects those files (code owners, a review by someone other than the author). | Does not show who approved anything: a record names no person. Does not show that duties are assigned. | [S1], 2026-11-19 |
| 5.33 | Protection of records | The records of the check are records too: a changed or removed one must not go unnoticed. | Package manifest: `members`, `bundle`, `required`. Ledger line: `previous`, `record-sha256`, `package`. Review record: `manifest-sha256`, `previous`. `verify` computes every digest and the manifest again; `period` checks the chain of the ledger. | Where packages and the ledger are kept, who can write there, how long they are kept, and the digest of the last ledger line kept in another place. | Supports the detection of a change, not its prevention. Does not show storage, access or retention. No signature is made. | [S1], 2026-11-19 |
| 8.4 | Access to source code | The files a check is judged by are source code whose change has special weight. | Record: `policy.changed`, `files`, `subject.base`, `subject.head`, `subject.checked`. Each changed file is named with the SHA-256 of its content in the commit checked. | The access rights, the branch protection and the audit log of the code host. | Does not show who can read or write the repository, nor that access is restricted. | [S1], 2026-11-19 |
| 8.25 | Secure development life cycle | Rules for development count when they are applied to each change, not once. | Record: `run`, `subject`, `result`, `eligible`, `eligible-reasons`. Ledger line: `verdicts`, `eligible`. Period report: `runs`, `not-eligible`, `gaps`. One record per change, and for a period every run with the commits of the default branch that have none. | The team's written rules for development, of which this check is one step, and the evidence for the other steps. | Records that this check ran and how it ended. Does not show that a life cycle is defined or followed as a whole. | [S1], 2026-11-19 |
| 8.27 | Secure system architecture and engineering principles | A team that declares architecture rules (layers, boundaries, capabilities) has to show that changes are held to them. | Record: `boundaries.introduced`, `boundaries.existing`, `baseline.verdict`, `baseline.entries`, `baseline.past`, `coverage.parts`, `coverage.not-seen`. Violations a change introduces, violations that were there before, each accepted break with owner, reason and end date, and the parts that were not checked. | The principles themselves, why the declared rules express them, and the decision behind each accepted break. | Checks the dependency rules the team declared. Does not show that the principles are sound, complete or about security. A graph built from source text does not see reflection or imports built from strings. | [S1], 2026-11-19 |
| 8.29 | Security testing in development and acceptance | The tests a change must pass are declared, and that they ran is recorded. | Record: `tests.required`, `tests.to-run`, `tests.evidence`, `tests.step-outcome`, `tests.reports`, `tests.manifest`. Package manifest: `checks`. Each required test with every recorded case, how the test step ended, and where each report comes from. | That the required tests include security tests, what they assert, the acceptance criteria, and the results of testing done outside the job. | Records that named tests ran and how each case ended. Does not show what a test asserts, nor that it is a security test. | [S1], 2026-11-19 |
| 8.32 | Change management | Every change of the code base goes through the same recorded check. | Record: `subject`, `files`, `boundaries`, `baseline`, `tests`, `coverage`, `policy`, `eligible`. Package manifest: `state`, `members`. Period report: `runs`, `not-eligible`, `policy-changes`, `gaps`. | The authorisation and approval of each change, the change procedure, the records of deployment and of emergency changes. | A record per change of the repository, not of a production system. Does not show that a change was authorised, deployed or rolled back. | [S1], 2026-11-19 |

## ISO/IEC 42001:2023, Annex A

Not promised: AI governance, impact assessment, accountability, or the behaviour of an agent at run
time, none of which a code graph shows. What a run gives is technical evidence for the life cycle and
the change of systems with a model in them: the code around the model, its prompts, tool definitions
and tests change like any other code.

The entries of Annex A are not part of the publisher's public preview [S2]. The identifiers and titles
below were checked against two public crosswalks, [S3] and [S4], which agree on them; they were not
checked against Annex A itself.

| Control | Title | Why it concerns a software team | Evidence a run gives | Still needed from elsewhere | Limit | Source, checked |
|---|---|---|---|---|---|---|
| A.4.2 | Resource documentation | A change of a system with a model in it is judged against one version of the model, the prompts, the tool definitions and the test data. | Package manifest: `stated`, `tool`. The versions the job states when it packs (build, model, prompt, tool-schema, test-data), kept as stated, and the release of the tool that built the graph. | The documentation of the resources themselves: data, tooling, computing and people. | Records what the job states, as stated. Does not show that a stated version is the one in use, and documents no resource. | [S3] [S4], 2026-11-19 |
| A.6.2.3 | Documentation of AI system design and development | Design decisions written as architecture rules can be held against the code at each change. | Record: `boundaries.introduced`, `boundaries.existing`, `coverage.parts`. Package manifest: `policy`. The rules as the commit checked holds them, and whether a change breaks one. | The design documentation itself: choices of model and method, data, evaluation. | Shows whether the code keeps the declared rules. Does not show that a design is documented, nor anything about a model. | [S3] [S4], 2026-11-19 |
| A.6.2.4 | AI system verification and validation | The tests of the code around a model are part of verifying the system, and their running is recorded per change. | Record: `tests.required`, `tests.to-run`, `tests.evidence`, `tests.step-outcome`, `tests.reports`. Package manifest: `checks`, `stated`. | The evaluation of the model itself, the validation against the intended use, the criteria and who accepted the results. | Records that named tests ran and how each case ended. Does not show that the tests evaluate a model, nor that the system is valid for its use. | [S3] [S4], 2026-11-19 |
| A.6.2.5 | AI system deployment | A release can be tied to the record of the change it ships. | Record: `eligible`, `eligible-reasons`, `subject.checked`. Package manifest: `state`, `bundle`. Review record: `decision`, `reviewer`, `manifest-sha256`. | The deployment plan, the release decision and the records of the deployment itself. | A record of a change before release. Does not show that anything was deployed, nor how a deployed system behaves. | [S3] [S4], 2026-11-19 |

## SOC 2: 2017 Trust Services Criteria (with revised points of focus, 2022)

Not promised: a positive report, and no conclusion on operating effectiveness from one passing snapshot.
What the tool gives is records over a period, with the failures and the reviews included (see "Evidence
over a period" in the README).

The criteria have identifiers and no titles of their own; the second column gives the heading the
criterion stands under (the sources print the headings of CC4 and CC5 in capitals). Rows are at the
level of a criterion: no point of focus is named.

| Criterion | Heading | Why it concerns a software team | Evidence a run gives | Still needed from elsewhere | Limit | Source, checked |
|---|---|---|---|---|---|---|
| CC4.1 | Monitoring Activities | A check that runs on every change can be evaluated over time only when its failures and its gaps are kept. | Period report: `runs`, `not-eligible`, `policy-changes`, `accepted-breaks`, `gaps`, `defects`. Ledger line: `sequence`, `previous`. | The entity's own evaluations, who performed them and what was done about a deficiency. | A count of what was recorded in a period. Does not conclude that a control operated effectively, and is not an evaluation by management. | [S5] [S6], 2026-11-19 |
| CC5.2 | Control Activities | The check is an automated control over the development of technology. | Record: `boundaries.introduced`, `tests.evidence`, `coverage.complete`, `policy.changed`. Ledger line: `verdicts`. | The selection of the control, its design, and the other control activities over technology. | Records one automated check. Does not show that control activities were selected or that they cover the risks. | [S5] [S6], 2026-11-19 |
| CC8.1 | Change Management | Each change of the code base leaves a record of what was checked, tested and left out. | Record: `subject`, `files`, `tests.required`, `tests.evidence`, `boundaries.introduced`, `policy.changed`, `eligible`. Package manifest: `checks`, `members`. Review record: `decision`, `reviewer`. Period report: `runs`, `not-eligible`, `gaps`. | The authorisation, approval and deployment of each change, the population of changes of the period, and the records of emergency changes. | Records of changes of the repository. Does not show authorisation or deployment, and a record that passed once shows nothing about a period. | [S5] [S6], 2026-11-19 |

## Sources

Each was read on 2026-11-19.

- [S1] ISO/IEC 27002:2022, third edition 2022-02, corrected version 2022-03: the table of contents in
  the preview the publishers give out, which lists every control of clauses 5 to 8 with its number
  and title.
  `https://www.vde-verlag.de/iec-normen/preview-pdf/info_isoiec27002{ed3.0}en.pdf`
  The preview of ISO/IEC 27001:2022 (third edition 2022-10) names Annex A, "Information security
  controls reference", and does not hold its table:
  `https://www.vde-verlag.de/iec-normen/preview-pdf/info_isoiec27001{ed3.0}en.pdf`
- [S2] ISO/IEC 42001:2023, first edition 2023-12: the preview the publishers give out. Its table of
  contents names Annex A, "Reference control objectives and controls", and does not hold its entries.
  `https://www.vde-verlag.de/iec-normen/preview-pdf/info_isoiec42001{ed1.0}en.pdf`
- [S3] The crosswalk from the NIST AI Risk Management Framework to ISO/IEC 42001 that the NIST AI
  Resource Center lists (`https://airc.nist.gov/airmf-resources/crosswalks/`). It was written against
  the final draft of the standard and names the entries by the numbers of Annex B, which mirror those
  of Annex A (B.6.2.4 for A.6.2.4).
  `https://airc.nist.gov/docs/NIST_AI_RMF_to_ISO_IEC_42001_Crosswalk.pdf`
- [S4] The crosswalk between ISO/IEC 42001:2023 and the testing framework of the AI Verify Foundation,
  which names the entries by their Annex A numbers (linked from
  `https://aiverifyfoundation.sg/resources/`).
  `https://aiverifyfoundation.sg/wp-content/uploads/2025/10/Crosswalk-AIV-and-ISO42001-final.pdf`
- [S5] AICPA, TSP Section 100, 2017 Trust Services Criteria for Security, Availability, Processing
  Integrity, Confidentiality, and Privacy. The publisher's page names the current print "2017 Trust
  Services Criteria (With Revised Points of Focus - 2022)" and gives the file to a registered reader:
  `https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022`
  The identifiers and the headings were read in the print of the same section that includes the
  updates of March 2020 (SHA-256 of the file read:
  `1f6b5798d77d8521846db321af262cf049988889928c1e6c5e3e832de54e1c8d`), not in the print of 2022.
- [S6] The crosswalk between the 2017 Trust Services Criteria and the NIST Privacy Framework in the
  resource repository of that framework, whose sheet is headed "2017 Trust Services Criteria
  (Inclusive of 2022 updates)" and lists every criterion under its heading.
  `https://www.nist.gov/itl/applied-cybersecurity/privacy-engineering/american-institute-certified-public-accountants-aicpa`

## For the reviewer

- ISO/IEC 27001:2022: the numbers and titles come from the table of contents of ISO/IEC 27002:2022.
  Confirm against your copy that Annex A lists the same seven under the same numbers and titles, and
  whether an amendment to either standard changes one of them.
- ISO/IEC 42001:2023: no row was checked against Annex A itself. Confirm the four identifiers and
  titles against your copy, or drop the rows.
- SOC 2: the identifiers and headings were read in the print of March 2020 and in [S6], not in the
  print with the revised points of focus of 2022. Confirm them there. The publisher has published
  exposure drafts of revised criteria for 2027 (its page of 2026-11-16); this page is written against
  the criteria of 2017.
- For every row: whether the evidence named supports the part of the control it is put next to, and
  whether the limit is stated strongly enough.
