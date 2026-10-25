// The home's words, read by the page and by its Markdown twin, so the two never drift apart.

export const hero = {
  title: 'One app for every coding agent you use',
  lede: 'Atlas is an open-source personal agent. It runs Claude Code, Codex, the Gemini CLI and DeepSeek on all your accounts and machines, and learns how you work as it goes.',
  note: 'Free and open source, under the MIT license.'
}

export interface Chapter {
  id: 'accounts' | 'route' | 'machines' | 'memory' | 'close'
  title: string
  body: string[]
}

/** The chapters the orrery tells, in the order it tells them. */
export const chapters: Chapter[] = [
  {
    id: 'accounts',
    title: 'Your coding agents, on every account you have',
    body: [
      'Claude Code, Codex, the Gemini CLI and DeepSeek run as you installed them, with your settings and your own login.',
      'Personal, company A, company B: each account keeps its own folder, history and plugins, and they all run side by side.'
    ]
  },
  {
    id: 'route',
    title: 'When one reaches its limit, the work moves on',
    body: [
      'Give a session a route: the accounts and harnesses it may use, in order. When an account hits its plan’s limit, the next one takes over without losing the thread.'
    ]
  },
  {
    id: 'machines',
    title: 'Every machine you have, from one window',
    body: [
      'Install Atlas on a home lab, a VPS or the office PC, then open it from the app on your laptop, over Tailscale or SSH.',
      'The Atlas over there does the work, with that machine’s files, keys and approvals.'
    ]
  },
  {
    id: 'memory',
    title: 'It learns how you work',
    body: [
      'Every few messages, a copy of the agent rereads the conversation and keeps what it learned: who you are goes to your profile, how to do a task goes to a skill.',
      'It is all plain text you can open and edit.'
    ]
  },
  {
    id: 'close',
    title: 'Everything it knows stays with you',
    body: [
      'No account, no telemetry and no cloud of ours. Your profile, memory, conversations and keys live in a folder on your machine, and your coding agents keep their own logins.'
    ]
  }
]

/** The meters of the route chapter: the five-hour window of each account on the route. */
export const route = {
  caption: 'Five-hour window',
  stops: [
    { name: 'Claude Code', account: 'personal' },
    { name: 'Claude Code', account: 'company' },
    { name: 'Codex', account: 'main' }
  ],
  handoff:
    'The next one starts with a handoff: the task, your latest instruction, the steps not done, the files changed and what happened so far.'
}

/** What the memory chapter shows the review keeping. */
export const memory = {
  entries: [
    { kind: 'profile', mark: '+', text: 'Prefers short answers, in Portuguese' },
    { kind: 'notes', mark: '+', text: 'The tests run with uv run pytest' },
    { kind: 'skill', mark: '~', text: 'release-notes: list breaking changes first' },
    { kind: 'proposal', mark: '?', text: 'Replace “deploys on Fridays” with “deploys on Thursdays”' }
  ],
  caption: 'Anything that would replace or remove a memory waits for your approval.'
}

export const gate = {
  title: 'Every command passes the approval gate',
  body: 'Atlas runs commands on your machine, so each one is checked before it runs. Some never run, whatever the mode, and anything that acts outside your machine on your behalf asks you first.',
  commands: [
    { command: 'npm test', verdict: 'Runs', detail: 'An ordinary command goes straight through.' },
    {
      command: 'git push --force',
      verdict: 'Reviewed',
      detail:
        'A dangerous pattern. Unless auto mode is on, a reviewer model reads it first, and what it is unsure of comes to you.'
    },
    {
      command: 'gh pr merge 42',
      verdict: 'Asks you',
      detail: 'It acts outside your machine, so it asks every time, even in auto mode.'
    },
    { command: 'rm -rf ~', verdict: 'Never runs', detail: 'Blocked in every mode, whatever was approved before.' }
  ]
}

export const catalogue = {
  title: 'Also in Atlas',
  items: [
    {
      name: 'Routines',
      text: 'Scheduled prompts and commands, written in plain words (“every weekday at 9”) or cron.'
    },
    {
      name: 'Bots',
      text: 'Named agents with their own mission, memory, skills and routines. They write to each other.'
    },
    { name: 'Telegram', text: 'Talk to Atlas from your phone, and answer your coding agents’ questions from it.' },
    { name: 'Voice', text: 'Dictate into any program on your computer, send audio, or talk live, on your OpenAI key.' },
    { name: 'A browser of its own', text: 'Atlas drives its own Chrome, and you can take over at any moment.' },
    {
      name: 'Terminal and previews',
      text: 'A terminal on each machine, and previews of what the agent builds, in the side panel.'
    },
    {
      name: 'Plugins',
      text: 'Add skills, commands, hooks, MCP servers, tools, channels and new coding agents.'
    },
    { name: 'Backups', text: 'One zip of your data, never the keys, and a restore that checks it first.' },
    {
      name: 'Your choice of model',
      text: 'OpenAI, Anthropic, Google Gemini or OpenRouter with your key, or local models through Ollama and vLLM.'
    }
  ]
}

export const install = {
  title: 'Install Atlas',
  steps: [
    'Run the line. The installer brings its own Python, Node and tools, checked against pinned hashes, then builds the app and opens it.',
    'Paste an API key from OpenAI, Anthropic, Google Gemini or OpenRouter when the app asks for one, and start talking.',
    'Turn on Claude Code, Codex, the Gemini CLI or DeepSeek under Plugins in Settings. Each one you have installed shows up in the Executors tab.'
  ],
  update: 'Run the same line again to update, or type atlas update. If a step fails, it goes back to where it was.'
}
