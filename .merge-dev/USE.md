# Resolutions for `git merge origin/dev` (verified: full suite 15/16, workspace 27/27)

After the merge stops on conflicts:

    git checkout --theirs VERSION                                  # 0.0.84-lpb-dev
    git checkout --ours   Dockerfile lpb.stack.env lpb.stack.dev.env lpb.stack.main.env   # pi 1.2.0
    cp .merge-dev/SKILL.md                          .pi/skills/localpibox-repo-workflow/SKILL.md
    cp .merge-dev/test_localpibox_workspace.py     scripts/
    git add VERSION Dockerfile lpb.stack.*.env .pi/skills/localpibox-repo-workflow/SKILL.md scripts/test_localpibox_workspace.py
    git commit && python3 scripts/test_localpibox.py     # then push, land on dev, bump

Then delete this directory: rm -rf .merge-dev
