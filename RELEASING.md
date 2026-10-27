# Releasing agy-team

Versions follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`,
tagged `vMAJOR.MINOR.PATCH`. What the version promises is the public surface
listed under "Versioning" in the README.

| change | bump |
|---|---|
| anything that can break a user, host adapter or client relying on the public surface: a removed or renamed tool, argument, CLI flag, env var, roster field or record field; a changed meaning or default that changes behaviour | **major** |
| new tools, flags, fields, runners, behaviour behind something new; deprecations | **minor** |
| fixes that change no documented behaviour | **patch** |

When unsure whether something breaks, it is major. A deprecation is announced
in a minor release and removed in the next major.

## Steps

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new heading,
   `## [X.Y.Z] - YYYY-MM-DD`, and update the compare links at the bottom.
2. Set `__version__ = "X.Y.Z"` in `agyteam/__init__.py` (the only place the
   version is written; `pyproject.toml` reads it).
3. Run the suite: `.venv/bin/python -m pytest -q`. `evals/test_version.py`
   fails if the version, the changelog and the tag disagree.
4. Commit (`Release X.Y.Z`), tag, push both:

   ```bash
   git tag -a vX.Y.Z -m "agy-team X.Y.Z"
   git push origin main vX.Y.Z
   ```

5. On GitHub, *Releases → Draft a new release*, choose the tag, and paste
   that version's CHANGELOG section as the notes.
6. Reinstall the plugin (`bash plugin/install.sh`) and check
   `python -m agyteam.install_check --drift <plugin dir>`.

Downstream code should pin a tag, not `main`:

```bash
pip install "agyteam @ git+https://github.com/roelle/agy-team.git@v3.11.1"
```
