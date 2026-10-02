# Toolbox

Bash and Python scripts for Linux administration, automation, and OpenShift management.

A collection of helpers for repetitive tasks, troubleshooting, and day-to-day operations. Scripts are designed to be small, readable, and easy to adapt.

## Repository structure

```text
toolbox/
├── linux/          # Linux administration and troubleshooting
├── openshift/      # OpenShift administration and cluster utilities
├── lib/            # Shared functions and modules
├── docs/           # Usage notes and examples
└── README.md
```

## Requirements

Requirements vary by script. Common dependencies include:

- **Bash** or **Python 3**
- **OpenShift CLI (`oc`)** for OpenShift scripts
- **jq** for scripts that process JSON
- Access to the target systems or cluster with the necessary permissions

Check each script’s documentation for specific dependencies and supported versions.

## Getting started

Clone the repository:

```bash
git clone https://github.com/<your-username>/toolbox.git
cd toolbox
```

Review the script and its prerequisites before running it. Invoke Bash and Python scripts directly with their interpreter:

```bash
bash linux/<script-name>.sh
python3 linux/<script-name>.py
```

> Replace the placeholders above with your GitHub username and an actual script name.

## Working with OpenShift

Log in to the intended cluster using your normal authentication workflow. Before running a script, verify your current context, API endpoint, identity, and project:

```bash
oc config current-context
oc whoami --show-server
oc whoami
oc project
```

Use the least privileges needed for the task. A script may operate beyond the current project, so review its scope before execution.

## Safety

**Review scripts before running them, especially against production systems.**

- Test in a non-production environment first.
- Confirm the target host, cluster, project, and resources.
- Check whether the script changes or deletes anything.
- Use dry-run options where available; do not assume every script supports them.
- Back up important data and configuration before disruptive operations.
- Never commit passwords, tokens, kubeconfig files, or other credentials.

## Script conventions

New scripts should:

- Have a clear, descriptive filename.
- Explain their purpose, usage, dependencies, and side effects.
- Validate required arguments and dependencies.
- Provide useful error messages and meaningful exit codes.
- Avoid hard-coded credentials and environment-specific values.
- Document required permissions and the scope of any changes.
- Request confirmation for destructive actions where practical.

## Contributing

Fixes, improvements, and new helpers are welcome.

Keep contributions focused and include:

- A brief explanation of the problem the script solves.
- Usage examples and required dependencies.
- Testing details, including relevant Linux or OpenShift versions.
- Any known limitations or operational risks.
<!-- Add a LICENSE file and replace this section with your chosen license. -->

A license has not yet been selected.
