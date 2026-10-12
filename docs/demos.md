# Workflow demos

Concrete demonstrations live in [`demos/`](../demos/). The reusable runner and
editing instructions live separately in [the demo template guide](demo-template.md).

**Use at your own risk.** Anyone executing these scripts, including modified
copies, is responsible for reviewing the commands, choosing the target environment
and accepting the consequences. Scripts are provided as-is, without warranty;
authors and contributors accept no liability for damage, data loss or service
interruption arising from their use. A demo is not a sandbox or a dry-run.

## Labctl workflow

[`demo-labctl.sh`](../demos/demo-labctl.sh) demonstrates:

- Cloning Labctl and installing it with `pipx`.
- Listing images and downloading `rocky:9`.
- Creating lab `LX001` and inspecting its properties.
- Listing VMs and opening an SSH session to the lab's `node` VM.
- Grading the lab and removing it with `labctl lab rm --force LX001`.

### Prerequisites and preparation

Use Bash 3.2+ and install Git, pipx, and the current
[Labctl prerequisites](https://github.com/rthdev/labctl), including the required
virtualization environment and permissions. Ensure `labctl` will be on PATH after
installation. Cloning, installation and image download require network access;
lab creation consumes local resources and disk space. The runner prints its
prerequisite description but does not validate the environment.

Read the entire script and adapt its `demo()` sequence before running it:

- `~/wip` must exist, and cloning expects `~/wip/labctl` not to exist already.
  Change the working directory or installation steps to suit your environment.
- Confirm the selected Labctl environment is disposable and lab `LX001` is not
  an existing lab you need to preserve. Change the lab ID consistently if needed.
- The final `--force` removal is destructive and bypasses Labctl's confirmation.
  Do not approve it unless the target lab and its data can be discarded.
- Verify commands against the Labctl version you install: the demo clones the
  upstream default branch rather than pinning a release.

### Running and controls

From the toolbox repository root, local help is safe to inspect without starting
any Labctl operations:

```bash
bash demos/demo-labctl.sh --help
```

Only after reviewing and adapting the script, start it in an interactive terminal:

```bash
bash demos/demo-labctl.sh
```

Press **Enter** to continue or execute the displayed command. Type **q** and Enter
at a runner pause to quit; end-of-input also quits. Interactive child commands
(such as SSH) receive terminal input themselves: leave the SSH session to resume
the demo. Do not pipe automatic answers into the runner.

Commands execute in the same shell, so directory changes persist. Failures are
shown but **do not stop the demo**: after a failed `cd`, for example, a subsequent
command can run in the wrong directory. Quit and investigate rather than blindly
continuing. Completion or quitting can return success even after command failures.
Nothing automatically rolls back installations, downloads or labs when you quit.
Review and clean up any completed actions explicitly.
