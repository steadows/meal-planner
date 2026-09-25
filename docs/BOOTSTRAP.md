# Bootstrap notes

How this repo was set up, and how the personal/work GitHub split works. The script that did it lives in the bootstrap bundle (`bootstrap.sh`), not in this repo.

## Two GitHub accounts at once

| Where you are | git commits as | git push uses | `gh` uses |
| --- | --- | --- | --- |
| `~/meal-planner` (and its worktrees) | steadows, noreply email | steadows token via `gh auth git-credential` | steadows (`~/.config/gh-steadows`) |
| Anywhere else | your global identity | your usual credential helper | your usual `gh` login (`~/.config/gh`) |

Three small pieces make that work, and none of them switch anything globally:

1. **`~/.config/gh-steadows/`** — a separate gh config dir holding only the steadows login. Created with `GH_CONFIG_DIR=~/.config/gh-steadows gh auth login`.
2. **`~/.gitconfig-steadows`** — name, email, a credential helper that reads the steadows token, and `ghauth.configdir`. It is pulled in by one line in `~/.gitconfig`: `includeIf "gitdir:~/meal-planner/"`. Git applies it to the main checkout and every worktree of this repo.
3. **`~/.local/bin/gh`** — a four-line wrapper. If the current repo's git config names a `ghauth.configdir`, it sets `GH_CONFIG_DIR` and runs the real gh; otherwise it runs the real gh untouched. `~/.local/bin` is put first on `PATH` in `~/.zshenv`, so non-interactive agent shells get it too.

Check it:

```sh
(cd ~/meal-planner && gh auth status && git config user.email)   # steadows
(cd ~ && gh auth status)                                          # work account
```

To add another personal repo to the steadows side, add one more `includeIf` line pointing at `~/.gitconfig-steadows`.

## Undo

```sh
git config --global --unset-all 'includeIf.gitdir:'"$HOME"'/meal-planner/.path'
rm ~/.gitconfig-steadows ~/.local/bin/gh
# and delete the "meal-planner bootstrap" line from ~/.zshenv
GH_CONFIG_DIR=~/.config/gh-steadows gh auth logout && rm -rf ~/.config/gh-steadows
```

## agent-brain seeding

`brain init` scaffolded `.brain/`, then each lane was registered with `brain new-feature <lane>` from its `feat/<lane>` branch, and the presence notes were filled in from the plan. The dependency arrows in PLAN.md → Concurrency lanes are `waiting-on` notes in `.brain/connections/`; the contracts ownership rule is a `shared-rule` note. All `feat/<lane>` branches start at the seeded `main`.
