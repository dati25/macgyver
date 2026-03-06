# Rossum Implementation Discovery Checklist

Use this checklist to systematically discover all components of a Rossum implementation. Look for these file types and configuration patterns:

## Configuration Files to Find

- **Schema definitions** — JSON files containing `category`, `datapoint`, `multivalue`, `section` keys
- **Hook/extension configurations** — Serverless functions, webhooks, connectors (look for `hook_type`, `config`, `sideload`)
- **Queue and workspace settings** — Queue configuration with `default_score_threshold`, `automation_level`, `automation_enabled`
- **Export pipeline configurations** — Export objects, SFTP/S3 destinations, export evaluators
- **Master Data Hub** — Datasets, matching rules, matching configurations
- **Business rules validation** — Rule sets with conditions and actions
- **Formula fields and TxScript** — Fields with `formula` key, TxScript expressions, serverless function code
- **Inbox configurations** — Email routing, filtering, bounce settings
- **Sandbox/deployment manager** — `mapping.yaml`, `credentials.yaml` files
- **Existing documentation** — README files, inline comments, configuration notes

## Discovery Commands

Useful glob patterns:
- `**/*.json` — All JSON configuration files
- `**/*.py` — Python serverless function code
- `**/*.yaml` or `**/*.yml` — Deployment manager configs
- `**/mapping.yaml` — Sandbox mapping files
- `**/credentials.yaml` — Credential templates

Useful grep patterns:
- `"category"` or `"datapoint"` — Schema files
- `"hook_type"` — Extension configurations
- `"default_score_threshold"` — Automation settings
- `"formula"` — Formula field definitions
- `"rir_field_names"` — AI extraction field mappings
- `"automation_blocker"` — Automation control logic
- `"match_config"` or `"dataset"` — Master Data Hub configurations
