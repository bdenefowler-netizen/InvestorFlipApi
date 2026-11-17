# County upload headers and behavior

Select the source in Add before uploading CSV, XLS/XLSX, or a ZIP of those formats.
Spaces, underscores, and capitalization in header names are normalized. Original
columns remain in raw_import_row. No admin key is required for these upload routes.

## Property identity

An existing TAD Account # takes priority and retrieves saved county identity fields.
Without TAD, an exact normalized Legal Description is tried first. Punctuation and
case are ignored; lot/block numbers and word order are preserved. When legal is
absent, address matching requires city or ZIP. Ambiguous matches are not selected.
Matching uses saved county records, not a live external API. Unmatched legal-only
leads remain stored; no street address is invented.

| Source | Supported source columns |
| --- | --- |
| TAD | TAD Account # / Account Number / Account ID, Legal Description, Property Address / Situs Address, Owner / Owner Name |
| Tax | Property Address, TAD Account #, APN/Parcel, Owner, Legal Description, Current Due, Prior Due, Total Due, Delinquency Date |
| Pre-Foreclosure | Current_Owner, Sell_Date / Sale Date, Filled_Date / Filed Date, Legal_Description, Property_Address |
| Probate | Decedent / Decedant, Applicant, Case_Type, Date_Filed, Case_Number, Decedent_Property_Address, Legal Description |
| Code Violations | Violation_Address, City, State, ZIP, Violation_Current_Status / Case_Current_Status / Status, Case_ID, Violation_ID, Complaint_Type_Description |
| Tax Lien | Grantor, Grantee, Doc_Type, Court_Case, Property_Address, Legal Description, Total_Due, DELINQUENCY DATE |
| Tax Auction | cause_number, tad_account_number, status / Sale Status, Sale Date, Legal Description |
| Repair Notice | case_id, created, doc_type, grantor, Owner, city, legal_description, Complain_Type, Violation_Address |

Code imports accept only recognized open states: Open, Active, In Progress,
Pending, Under Investigation, Notice Issued, Reopened. An explicit violation
status takes precedence over the case status. Closed/unknown/missing statuses
are rejected with a reason. Repair notices are a separate source: a notice does
not prove an open code case. Recorded tax liens do not imply a scheduled auction.
Legacy tax-lien files with an explicit sale status retain their existing treatment.

Lead upload summaries report actual inserted/updated/rejected counts. TAD, Tax,
and Probate keep their existing staging path, labeled as staged. Paid provider
enrichment and calculator work are separate. Probate case rows remain
in the existing case workspace; receiving a case does not prove parcel matching.
