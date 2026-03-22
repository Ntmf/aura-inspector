# Batch Scanning & Ignore List Usage

This document covers the batch scanning, consolidated reporting, and ignore list features added to aura-inspector.

## Quick Start

```bash
python3 aura_cli.py -b orgs.csv -i ignore.txt -o results/ -k
```

## Batch Scanning

### Input CSV Format

Create a CSV file with your org URLs. Only the `url` column is required — all other columns are optional.

| Column    | Required | Description |
|-----------|----------|-------------|
| `url`     | Yes      | Root URL of the Salesforce Experience Cloud site |
| `cookies` | No       | Session cookies (e.g., `sid=abc123`) for authenticated scanning |
| `app`     | No       | App path (e.g., `/myApp`) if the org has custom apps |
| `aura`    | No       | Aura endpoint path override (e.g., `/s/sfsites/aura`) |
| `context` | No       | Pre-supplied aura.context for POST requests |
| `token`   | No       | Pre-supplied aura.token for POST requests |
| `no_gql`  | No       | Set to `true` to skip GraphQL checks for this org |

**Example `orgs.csv`:**

```csv
url,cookies,app,aura,context,token,no_gql
https://org1.my.site.com,,,,,,
https://org2.my.site.com,sid=abc123,,,,,
https://org3.force.com,,/myApp,,,,true
https://org4.my.site.com,,,,,,
```

### Running a Batch Scan

```bash
# Basic batch scan (unauthenticated, all orgs)
python3 aura_cli.py -b orgs.csv -o results/

# With ignore list and TLS certificate bypass
python3 aura_cli.py -b orgs.csv -i ignore.txt -o results/ -k

# With verbose logging
python3 aura_cli.py -b orgs.csv -o results/ -v

# Only scan specific objects across all orgs
python3 aura_cli.py -b orgs.csv -o results/ -l Account,Contact,Case

# Through a proxy
python3 aura_cli.py -b orgs.csv -o results/ -p http://127.0.0.1:8080

# Disable GraphQL for all orgs (can also be set per-org in CSV)
python3 aura_cli.py -b orgs.csv -o results/ --no-gql

# Run with 5 parallel workers for faster scanning
python3 aura_cli.py -b orgs.csv -o results/ -w 5

# Parallel with ignore list
python3 aura_cli.py -b orgs.csv -i ignore.txt -o results/ -k -w 10
```

**Note:** `--batch-file` cannot be combined with `-u` or `-r`. The `--output-dir` flag is required in batch mode.

### Parallel Scanning

By default, orgs are scanned sequentially (one at a time). Use `-w`/`--workers` to scan multiple orgs in parallel:

```bash
python3 aura_cli.py -b orgs.csv -o results/ -w 5
```

This uses 5 threads to scan orgs concurrently. Each org still gets its own HTTP session, so there are no shared-state issues. Start with a low number (3-5) and increase if your network and the target Salesforce instances can handle it. Console output from parallel scans will be interleaved.

### Error Handling

If a scan fails for one org (e.g., unreachable URL, no aura endpoint, invalid session), the error is logged and the batch continues to the next org. Failed orgs are listed in both the console summary and the JSON report.

## Ignore List

The ignore list filters out objects you expect to be publicly accessible, reducing noise in your results.

### Format

A plain text file with one object API name per line. Lines starting with `#` are treated as comments. Matching is case-insensitive.

**Example `ignore.txt`:**

```
# Standard Salesforce objects that are expected to be public
Account
Contact
User
ContentDocument
CollaborationGroup

# Custom objects that are intentionally public
FAQ__c
Public_Article__c
```

### Behavior

- Ignored objects are **skipped entirely during scanning** — they are not queried for records, which saves time.
- Ignored objects are excluded from **both** per-org output and the consolidated report.
- The ignore list works in both single-org mode (`-u`) and batch mode (`-b`).

### Single-Org Mode with Ignore List

```bash
python3 aura_cli.py -u https://myorg.my.site.com -i ignore.txt -o results/
```

## Output

### Directory Structure

```
results/
├── consolidated_report.csv        # Aggregated results across all orgs
├── consolidated_report.json       # Detailed JSON with per-org breakdown
├── org1_my_site_com/              # Per-org output
│   ├── records/
│   │   └── summary.txt
│   ├── gql_records/
│   │   └── summary.txt
│   └── misc/
│       ├── recordlists.json
│       ├── homeurls.json
│       ├── csp_trusted_sites.json
│       └── custom_controllers.json
├── org2_my_site_com/
│   └── ...
└── ...
```

### Consolidated CSV

A spreadsheet-friendly overview sorted by number of orgs exposed (descending).

| Column           | Description |
|------------------|-------------|
| `object_name`    | Salesforce object API name |
| `total_org_count`| Number of orgs where this object has exposed records |
| `org_urls`       | Semicolon-separated list of affected org URLs |

### Consolidated JSON

A richer report for programmatic consumption:

```json
{
  "scan_date": "2026-03-22",
  "total_orgs_scanned": 412,
  "total_orgs_succeeded": 400,
  "total_orgs_failed": 12,
  "failed_orgs": [
    {"url": "https://orgX.my.site.com", "error": "Cannot reach the target URL"}
  ],
  "ignored_objects": ["account", "contact", "user"],
  "exposed_objects": {
    "Case": {
      "org_count": 15,
      "orgs": [
        {"url": "https://org1.my.site.com", "record_count": 250, "gql_count": 300},
        {"url": "https://org5.my.site.com", "record_count": 42, "gql_count": 0}
      ]
    }
  }
}
```

## Tips for Large-Scale Scanning

- **Start small:** Test with 2-3 orgs first to verify your CSV format and ignore list.
- **Use `-k`:** Many Salesforce orgs may have certificate issues; the insecure flag avoids scan failures from TLS errors.
- **Use verbose mode (`-v`)** for troubleshooting individual org issues.
- **Review failed orgs** in the JSON report and re-scan them individually if needed.
- **Build your ignore list iteratively:** After the first batch run, review the consolidated report and add commonly exposed but expected objects to the ignore list, then re-run.
