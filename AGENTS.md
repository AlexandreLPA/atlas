# Working on Atlas

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before writing code. Its rules are not suggestions:
CI enforces them. Name new concepts in [docs/GLOSSARY.md](docs/GLOSSARY.md) first.

- **Language.** Code, comments, logs and these docs are in English. The roadmap
  ([ROADMAP.md](ROADMAP.md)) is in Portuguese.
- **Nothing personal in the repository.** No real names, client names, home folders or conversation
  data in code, tests, fixtures or docs. User state lives in `ATLAS_HOME`.
- **Porting from Atlas V3.** V3 is a separate private repository. Code comes over only through the
  gate at the end of the architecture document: read it, check it, port it in English while fixing
  it, and bring its tests.
- **Commits.** The subject is in English. Its last paragraph, with the `Co-Authored-By` line, also
  carries a `Notes-pt-BR:` line: the subject said in Portuguese, for the update dialog of the app
  in Portuguese, which reads it with the subjects (`atlas.installation.check`).

## Commands

```bash
uv sync                    # install (Python 3.13 is managed by uv)
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pyright             # types, strict on src/
uv run lint-imports        # subject boundaries
uv run deptry src          # dependency hygiene
uv run pytest              # tests, with the coverage floor
uv run atlas serve         # run the server on 127.0.0.1:18790
docker compose up --build  # run in a container
docker build -t atlas:local . && ATLAS_TEST_IMAGE=atlas:local uv run pytest tests/docker --no-cov
                           # the image's tests: an empty container, a key, a conversation
uv run python scripts/gen_protocol.py   # the app's TypeScript protocol, after a protocol change
```

The desktop app, in `apps/desktop/` (Node 24):

```bash
npm ci                     # install
npm run check              # tsc, eslint, prettier, vitest
npm run test:e2e           # build, then the real app, atlas serve and the fake model, end to end
npm run dev                # run the app from source
```

All of them must pass before a commit.
