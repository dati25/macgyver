---
name: jira
description: This skill should be used when the user mentions a Jira ticket ID (e.g., "DC-1234", "PLAT-123"), asks to "look up a ticket", "check a Jira issue", "get ticket details", or references any Jira-related task lookup. Provides guidance for querying Jira tickets via the Atlassian MCP tool.
---

# Jira Ticket Lookup

## Usage

Use the `mcp__atlassian__getJiraIssue` tool with:

- `cloudId`: `rossumai-sandbox.atlassian.net` (the tool auto-resolves this to the UUID)
- `issueIdOrKey`: The ticket key (e.g., `DC-6635`, `PLAT-123`)
- `fields`: Use minimal fields to avoid large responses

## Default Fields (Minimal Lookup)

Include the `fields` parameter to avoid large responses. Here is a sensible default. Update if needed for a particular task.

```json
[
  "summary",
  "description",
  "status",
  "assignee",
  "reporter",
  "priority",
  "issuetype",
  "resolution",
  "created",
  "updated",
  "labels",
  "components",
  "parent"
]
```

This covers the essentials:

- **Basic info**: summary, description, issuetype, priority
- **Status**: status, resolution
- **People**: assignee, reporter
- **Time**: created, updated
- **Organization**: labels, components, parent

## Example

To look up ticket DC-1234:

```
cloudId: rossumai-sandbox.atlassian.net
issueIdOrKey: DC-1234
fields: ["summary", "status", "assignee", "reporter", "priority", "issuetype", "resolution", "created", "updated", "labels", "components", "parent"]
```
