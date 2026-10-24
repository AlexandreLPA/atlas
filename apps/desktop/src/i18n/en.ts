const STATUSES: Record<string, string> = {
  error: 'failed',
  ok: 'answered',
  running: 'running',
  silent: 'silent',
  unknown: 'unknown'
}

const EFFORTS: Record<string, string> = {
  high: 'High',
  low: 'Low',
  max: 'Max',
  medium: 'Medium',
  minimal: 'Minimal',
  none: 'Off',
  xhigh: 'Extra high'
}

/** Every text the screen shows, in English. Other languages override it (`catalog.ts`). */
export const en = {
  approval: {
    always: 'Always',
    conversation: 'This conversation',
    deny: 'Deny',
    guardianDenied: 'The guardian judged this dangerous. You may still allow it, once.',
    once: 'Allow once',
    reasonPlaceholder: 'Why not? (optional, the agent reads it)',
    title: (tool: string) => `Allow ${tool}?`
  },
  ask: {
    other: 'Other',
    otherPlaceholder: 'Your answer',
    pickSome: 'Choose any',
    skip: 'Skip',
    submit: 'Answer',
    title: (count: number) => (count === 1 ? 'The agent asks' : `The agent asks ${count} questions`)
  },
  secret: {
    byBot: (bot: string) => `For the bot ${bot}; every bot of this home reads this key.`,
    hide: 'Hide',
    overwrite: (when: string) => `A key is already stored, last written ${when}. Pasting replaces it.`,
    placeholder: 'Paste the key',
    save: 'Save',
    show: 'Show',
    skip: 'Skip',
    sharedWithBots: 'Every bot of this home reads this key.',
    title: (name: string) => `Atlas asks for the key ${name}`
  },
  bots: {
    advanced: 'Advanced',
    cancel: 'Cancel',
    confirmDelete: 'Delete it, with its memory and skills?',
    create: 'Create',
    delete: 'Delete',
    description: 'Description',
    descriptionPlaceholder: 'What should this bot help with?',
    edit: 'Edit',
    editTitle: 'Edit bot',
    empty: 'No bots yet. A bot is an agent with its own mission, memory and conversation.',
    emptyChat: (title: string) => `This is your conversation with ${title}. It never starts over.`,
    everything: 'Everything the home has',
    intro: 'A named agent with its own mission, memory, skills, conversation and routines.',
    mcp: 'MCP servers',
    mission: 'Mission and rules',
    missionHint: 'Blank: composed from the title and the description',
    model: 'Model',
    modelOfHome: "The home's model",
    name: 'Name',
    new: 'New bot',
    newTitle: 'New bot',
    nothingToPick: 'The home has none.',
    routines: (count: number) => (count === 1 ? '1 routine' : `${count} routines`),
    save: 'Save',
    skills: 'Skills of the library',
    tab: 'Bots',
    title: 'Title',
    tools: 'Tools'
  },
  attachments: {
    add: 'Attach files',
    drop: 'Drop files to attach them',
    failed: (name: string, why: string) => `${name} was not attached: ${why}`,
    open: (name: string) => `Open ${name}`,
    remove: (name: string) => `Remove ${name}`,
    size: (bytes: number) =>
      bytes < 1024
        ? `${String(bytes)} B`
        : bytes < 1024 * 1024
          ? `${(bytes / 1024).toFixed(0)} KB`
          : `${(bytes / 1024 / 1024).toFixed(1)} MB`,
    tooHeavy: (name: string, most: string) => `${name} is too heavy: this kind of file takes at most ${most}.`,
    uploading: (percent: number) => `Sending… ${String(percent)}%`,
    waiting: 'Wait for the files to finish sending'
  },
  composer: {
    slashCommands: 'Commands and skills',
    slashGroups: { command: 'Commands', skill: 'Skills' } as Record<'command' | 'skill', string>,
    autoMode: 'Auto mode: skip the guardian and the questions. The floors still block.',
    auto: 'Auto',
    autoModeLabel: 'Auto mode',
    defaulted: (label: string) => `${label} · default`,
    effort: 'Effort: how hard the model thinks (for this conversation)',
    effortLevel: (level: string) => EFFORTS[level] ?? level,
    model: 'Model (for this conversation, from its next turn)',
    noModel: 'No model: store a key with atlas secrets set',
    placeholder: 'Message Atlas',
    placeholderRunning: 'Steer the turn: this enters after the tools in course',
    placeholderTo: (name: string) => `Message ${name}`,
    send: 'Send',
    stop: 'Stop'
  },
  connection: {
    closed: 'Atlas is not answering. Trying again…',
    connecting: 'Connecting to Atlas…',
    failed: (reason: string) => `Could not reach Atlas: ${reason}`
  },
  conversations: {
    empty: 'No conversations yet.',
    new: 'New conversation',
    tab: 'Conversations',
    untitled: 'New conversation',
    via: (source: string) => `via ${source}`
  },
  homes: {
    backedUp: (path: string, size: string) => `Backed up to ${path} (${size}). Keys are never in a backup.`,
    backingUp: 'Backing up…',
    backup: 'Back up',
    backupTitle: 'Save a backup of this home: one zip with its settings, memory, skills and conversations',
    cancel: 'Cancel',
    checking: 'Checking the backup…',
    clone: 'Copy from',
    cloneHint: 'Takes its settings, models, MCP servers, keys, profile, memory and skills. Conversations start empty.',
    cloneNone: 'None (an empty home)',
    create: 'Create',
    creating: 'Creating…',
    dismiss: 'Close',
    emptyHint: 'Starts empty: Atlas will ask for a model key when it opens.',
    import: 'Import',
    importAction: 'Import',
    importFrom: (home: string, when: string, atlas: string, conversations: number) =>
      `A backup of the home ${home}, taken ${when} by Atlas ${atlas}, with ${String(conversations)} ${
        conversations === 1 ? 'conversation' : 'conversations'
      }.`,
    importHint:
      'It becomes a new home, and the home open now stays as it is. Keys are never in a backup, so the new home asks for one when it opens.',
    importing: 'Importing…',
    importTitle: 'Import a backup',
    importTooltip: 'Make a new home from a backup',
    inHome: (conversation: string, home: string) => `${conversation} (home ${home})`,
    intro: 'Each home is an Atlas of its own: its own settings, keys, memory, skills and conversations.',
    label: 'Home',
    name: 'Name',
    nameHint: 'Lowercase letters, digits, - and _',
    newHome: 'New home',
    newName: 'Name of the new home',
    newTitle: 'New home',
    remote:
      'This app talks to a server on another machine: make and import its homes in its terminal (atlas home create, atlas restore).',
    editProfile: 'Edit home',
    homes: 'Homes',
    menu: (home: string) => `${home}: switch home or edit it`,
    picture: 'Choose a picture',
    pictureRemove: 'Remove the picture',
    pictureUnreadable: 'That file is not an image this app can read. Choose a PNG, JPEG, GIF or WebP.',
    profileTitle: 'This home',
    profileHint: 'Its picture and name show at the foot of the sidebar. A backup carries the picture.',
    switchTo: 'Switch to another home',
    zipFilter: 'Atlas backup'
  },
  keys: {
    band: 'No model provider is connected, so Atlas cannot answer yet.',
    bandAction: 'Connect one',
    checking: 'Checking the key',
    connect: 'Connect',
    continue: 'Continue',
    describe: {
      anthropic: 'Direct access to Claude models, with their reasoning kept across tools.',
      gemini: 'Direct access to Google Gemini models.',
      openai: 'Direct access to OpenAI models.',
      openrouter: 'Hundreds of models behind a single key. A good first choice.'
    } as Record<string, string>,
    failed: (error: string) => `The key was not saved: ${error}`,
    getKey: 'Get a key',
    intro:
      'Atlas talks to a model with your own API key. Paste one to start: it is checked with the provider, ' +
      'at no cost, and kept encrypted on this machine.',
    later: "I'll choose later",
    placeholder: 'Paste the API key',
    saved: 'The key is saved.',
    stored: 'connected',
    title: 'Connect a model provider'
  },
  notification: {
    approval: 'Atlas asks for your approval',
    asks: 'Atlas has a question',
    done: 'Atlas answered',
    executor: (name: string) => `${name} is waiting for you`,
    secret: (name: string) => `Atlas asks for the key ${name}`
  },
  proposals: {
    adds: 'Adds',
    after: 'After',
    applied: 'Applied.',
    approve: 'Approve',
    back: 'Back to the conversation',
    before: 'Before',
    discard: 'Discard',
    empty: 'No memory proposals.',
    fromConversation: 'From the conversation',
    intro:
      'The background review would change these entries of the memory. Nothing changes until you approve; ' +
      'a change applies only while the entries it changes are as they were.',
    ok: 'OK',
    overwrote: 'Overwrote, whole:',
    readd: 'Ask Atlas to add back anything you still want.',
    removed: 'Removed:',
    removes: 'Removes',
    replaces: 'Changes',
    review: 'Review',
    title: 'Memory proposals',
    titleBotMemory: (bot: string) => `Change to the memory of the bot ${bot}`,
    titleMemory: 'Change to the memory',
    titleUser: 'Change to your profile',
    whole: 'See before and after in full'
  },
  plugins: {
    approve: 'Let in',
    pageGone: 'This page belonged to a plugin that is off now.',
    screenFailed: (name: string, why: string) => `The plugin screen ${name} failed: ${why}`,
    screenNotLoaded: (why: string) => `Its screen did not load: ${why}`,
    approved: 'let in',
    cancel: 'Cancel',
    consentIntro: 'Turning it on lets it:',
    defaultValue: (value: string) => `Default: ${value}`,
    empty: 'No plugins. Atlas ships some, and you can add one as a folder in the home, in plugins/.',
    enable: 'Turn on…',
    enableNow: 'Turn on',
    disable: 'Turn off',
    intro:
      'Plugins add to Atlas. They arrive turned off; turning one on shows what it may do, and nothing runs before you agree.',
    off: 'off',
    on: 'on',
    people: 'People',
    peopleIntro:
      'Who may talk to Atlas through a channel. Someone new gets a code; let them in here, and they send their message again.',
    noPeople: 'Nobody yet.',
    reload: 'Reload',
    reloadHint: 'Load the plugins that are on again from their files. Atlas also does it when a file of one is saved.',
    remove: 'Remove',
    reset: 'Default',
    running: 'running',
    save: 'Save',
    secretPlaceholder: 'Paste it here',
    settings: 'Settings',
    shipped: 'ships with Atlas',
    stored: 'stored',
    notStored: 'not stored',
    title: 'Plugins',
    waits: (code: string) => `waits with ${code}`,
    waiting: (count: number) => (count === 1 ? '1 waits' : `${count} wait`)
  },
  routines: {
    confirmRemove: 'Remove it?',
    empty: 'No routines yet. Ask Atlas to schedule one, or use atlas routines create in the terminal.',
    failed: (name: string) => `Routine ${name} failed`,
    finished: (name: string) => `Routine ${name} finished`,
    hideRuns: 'Hide runs',
    intro:
      'Prompts and commands Atlas runs on a schedule. Each run of a prompt is a conversation of its own; you are notified when one ends, unless it had nothing to say.',
    last: (status: string) => `last ${status}`,
    late: 'late',
    next: (time: string) => `next ${time}`,
    noRuns: 'It has not run yet.',
    open: 'Open',
    pause: 'Pause',
    paused: 'paused',
    remove: 'Remove',
    resume: 'Resume',
    runNow: 'Run now',
    showRuns: 'Runs',
    status: (status: string) => STATUSES[status] ?? status,
    title: 'Routines'
  },
  soul: {
    blank: "Blank, so Atlas's default stands in:",
    intro:
      'Who Atlas is and how it speaks, in every conversation: its SOUL.md. Write it in your own words, or ask Atlas to change it. A change applies from the next conversation.',
    loading: 'Reading SOUL.md…',
    missing: 'There is no SOUL.md yet. Write it below and save to make one; until then, Atlas is its default.',
    placeholder: "Who Atlas is and how it speaks. Leave blank to keep Atlas's default.",
    save: 'Save SOUL.md',
    saved: 'Saved. It applies from the next conversation.',
    saving: 'Saving…',
    title: 'Soul',
    unsaved: 'Unsaved changes'
  },
  settings: {
    about: 'About',
    aboutYou: 'About you',
    aboutYouHint:
      'Who you are, what you prefer and how you work: USER.md. Atlas reads it in every conversation and adds to it as it learns.',
    add: 'Add',
    addPlaceholder: 'A new entry',
    appliesNext: 'A change applies from the next conversation; an open one keeps what it started with.',
    approvalTimeout: 'Approval wait',
    approvalTimeoutHint: 'Seconds an approval waits for you before it counts as refused.',
    ask: 'Always ask first',
    askHint: 'Commands that ask you even in auto. One per line, * as a wildcard: git push*',
    askSkip: "Left out of Atlas's own rules",
    askSkipHint: 'Commands Atlas would ask about on its own that you allow: gh issue comment*',
    automatic: 'The first provider with a key',
    cancel: 'Cancel',
    checkUpdate: 'Look for an update',
    checking: 'Looking…',
    choose: 'Choose…',
    close: 'Close',
    defaultModel: 'Default model',
    defaultModelHint:
      "What a conversation that picked no model answers with, from its next turn. A conversation's own pick stays.",
    deny: 'Blocked commands',
    denyHint: 'Never run, not even in auto. One per line, * as a wildcard: git push --force*',
    edit: 'Edit',
    effort: 'Effort',
    effortDefault: "The model's default",
    empty: 'Nothing yet.',
    folder: 'Starting folder',
    folderHint: 'Where the agent starts, in new conversations. Blank, your home folder:',
    keys: 'Provider keys',
    keysHint: 'Each key is checked with its provider at no cost and kept encrypted on the machine of this home.',
    language: 'Language',
    languageHint: 'Of this computer. The window reloads to change it; no conversation stops.',
    languageSystem: "The system's",
    languages: { en: 'English', 'pt-BR': 'Português' } as Record<string, string>,
    locked: (variable: string) => `Set by ${variable}, which wins over config.toml.`,
    memory: 'Memory',
    memoryHint: 'What Atlas noted about your machine, your tools and their quirks: MEMORY.md.',
    model: 'Model',
    noUpdates: 'This installation does not update from the app.',
    proposals: (count: number) =>
      count === 1 ? '1 memory proposal waiting' : `${String(count)} memory proposals waiting`,
    provider: 'Provider',
    remove: 'Remove',
    save: 'Save',
    saved: 'Saved',
    saving: 'Saving…',
    sections: {
      executors: 'Executors',
      general: 'General',
      machines: 'Machines',
      model: 'Model',
      personalization: 'Personalization',
      plugins: 'Plugins',
      safety: 'Safety',
      workspace: 'Workspace'
    },
    theme: 'Look',
    themeDark: 'Dark',
    themeHint: 'Of this computer.',
    themeLight: 'Light',
    themeSystem: "The system's",
    title: 'Settings',
    upToDate: 'Atlas is up to date.',
    update: 'Update',
    updateAvailable: (count: number) =>
      count === 1 ? 'An update is available: 1 change.' : `An update is available: ${String(count)} changes.`,
    used: (used: number, limit: number) => `${used.toLocaleString('en')} of ${limit.toLocaleString('en')} characters`,
    version: (version: string) => `Atlas ${version}`,
    zone: 'Time zone of routines',
    zoneHint: "An IANA name, such as America/Sao_Paulo. Blank, the system's."
  },
  executor: {
    allow: 'Allow',
    answer: 'Answer',
    asks: (name: string) => `${name} asks`,
    deny: 'Deny',
    dismiss: 'Dismiss',
    other: 'Other',
    reason: 'Why (optional, it reads it)',
    wantsTo: (name: string, tool: string) => `${name} wants to use ${tool}`
  },
  subagents: {
    button: (agents: number, tasks: number) =>
      [
        agents === 0 ? '' : agents === 1 ? '1 agent' : `${agents} agents`,
        tasks === 0 ? '' : tasks === 1 ? '1 task' : `${tasks} tasks`
      ]
        .filter(Boolean)
        .join(' · '),
    close: 'Close',
    hint: (count: number) =>
      `${count === 1 ? '1 subagent working' : `${count} subagents working`} · click one for details`,
    map: 'Agent map',
    noOutput: 'No output yet.',
    noTool: 'starting',
    output: 'Output',
    parent: 'Atlas, this conversation',
    refused: 'It no longer takes corrections: its result is on the way.',
    send: 'Send',
    steer: 'Steer',
    steerPlaceholder: 'A course correction: it enters after the tools in course',
    stop: 'Stop',
    taskGone: 'It had already ended.',
    tasks: (count: number) =>
      `${count === 1 ? '1 background task' : `${count} background tasks`} · click one for details`,
    taskStatus: (status: string, code: null | number) =>
      ({
        completed: 'Completed',
        failed: `Failed · exit code ${String(code)}`,
        running: 'Running',
        stopped: 'Stopped'
      })[status] ?? status,
    tools: (count: number) => (count === 1 ? '1 tool' : `${count} tools`)
  },
  executors: {
    account: 'Change account',
    accountLabel: 'The account to log it into (its email)',
    atlasAnswers: 'Atlas answers',
    atlasAnswersNote: 'Atlas answers what it is sure of; the rest still comes to you.',
    auto: 'Automatic',
    autoNote: "It asks no permission, so Atlas's guardian is passed over.",
    autoWarning: (title: string) =>
      `${title} will approve its own permission requests in this session, without asking you: it may run commands and edit files. Its classifier still reviews each action, and your ${title} settings stay as they are.`,
    autoWarningCancel: 'Keep asking',
    autoWarningConfirm: 'Approve automatically in this session',
    autoWarningRules: (title: string) =>
      `It follows its own rules and your ${title} settings, so it may commit and push to this project's repository. To keep a command asking you, add an ask rule to your ${title} settings.`,
    archive: (folder: string) => `Archive ${folder}: hide it from the list`,
    archivedFolders: (count: number) => `Archived folders (${String(count)})`,
    archivedSessions: (count: number) => `Archived sessions (${String(count)})`,
    archiveSession: (title: string) => `Archive ${title}: hide it from the list`,
    deleteSession: (title: string) => `Delete ${title}`,
    deleteSessionNote: 'Delete this session? Its files leave this account for good; the other accounts keep theirs.',
    deleteSessionYes: 'Delete',
    unarchiveSession: (title: string) => `Bring ${title} back to the list`,
    byAtlas: 'by Atlas',
    cancel: 'Cancel',
    choose: 'Choose…',
    commands: 'Its commands',
    confirm: 'OK',
    confirmTurnOn: 'Turn it on',
    copyNote:
      'Your next message starts a copy: a session of its own, with everything until now. The original stays as it was.',
    effort: 'Effort: how hard the model thinks (from its next request)',
    elsewhere: (where: string) => `open in ${where}`,
    elsewhereBody:
      'Using it in two places at once can mix up its messages: each goes on from where it was, and later Claude Code follows one and forgets the other.',
    elsewhereCopy: 'Continue in a copy',
    elsewhereHere: 'Continue here anyway',
    elsewhereRead: 'Read only',
    elsewhereTitle: (where: string) => `This session is open in ${where}.`,
    empty: 'No sessions yet.',
    emptyFolder: 'No sessions in this folder.',
    emptySession: (title: string, folder: string) =>
      `A new session of ${title} in ${folder}. Your first message starts it.`,
    expired: (title: string) => `${title} no longer keeps this session.`,
    failed: (error: string) => `They could not be read: ${error}`,
    folder: 'Folder',
    folderPlaceholder: 'The folder, from your working folder or whole',
    harnesses: 'Harnesses on this computer',
    harnessesIntro:
      'Atlas hands work to the coding harnesses you already use, and shows their sessions here. Turn on the ones you want: each runs with your own login.',
    logIn: 'Log in',
    lookAgain: 'Look again: one installed, updated or logged in since is found',
    looking: 'Looking for them…',
    menu: (name: string) => `${name}: account and list`,
    model: 'Model (for this session, from its next request)',
    newSession: 'New session',
    newSessionIn: (folder: string) => `New session in ${folder}`,
    noneOn: 'No harness is turned on yet.',
    openSettings: 'Turn one on in Settings',
    notFound: 'not found on this computer',
    notLoggedIn: 'not logged in',
    on: 'on',
    open: 'Open',
    openTitle: 'Open its session in the executors tab',
    places: { cli: 'a terminal', 'claude-desktop': 'Claude desktop', 'claude-vscode': 'VS Code' } as Record<
      string,
      string
    >,
    placeholder: (title: string) => `Message ${title}`,
    placeholderWorking: 'It is working: this enters the turn running',
    readAgain: 'Read again',
    showArchived: 'Show archived folders and sessions',
    readOnly: 'Read only: nothing is sent from here.',
    readingFolders: 'Reading its folders…',
    readingSessions: 'Reading its sessions…',
    runsAs: (model: string) => `its last turn ran as ${model}`,
    start: 'Open',
    stop: 'Stop',
    tab: 'Executors',
    turnOn: 'Turn on',
    openInEditor: 'Open the project in VS Code',
    unarchive: (folder: string) => `Bring ${folder} back to the list`,
    untitled: 'New session'
  },
  accounts: {
    add: 'Add an account',
    adding: 'Add',
    cancel: 'Cancel',
    folder: (folder: string) => `The account ${folder}'s new sessions start on`,
    logIn: 'Log in',
    login: 'Its email (optional)',
    loginNote: (title: string) => `${title} opens its login on your browser: sign in with this account.`,
    main: 'main',
    mainAccount: 'Main account',
    nickname: 'Its nickname',
    nicknameNote:
      'It gets a folder of its own beside yours, with its own login, settings and sessions: letters, digits, - or _.',
    nicknamePlaceholder: 'company-b, personal…',
    of: 'The harness it is an account of',
    remove: (name: string) => `Remove ${name}`,
    removeNote: (name: string, folder: string) =>
      `Remove ${name}? Its folder (${folder}) stays as it is, with its login and its sessions; its projects start on the main account.`,
    session: 'Account: switching goes on in a copy of this conversation on the other account',
    sessionBusy: 'The account can be switched once the session stops working.',
    sessionTitle: "The session's account",
    switchChecking: 'Looking at that account…',
    switched: (name: string, tokens: null | number) =>
      tokens === null
        ? `Now on ${name}. The first message on this account loads the whole history; compacting first (/compact) loads less.`
        : `Now on ${name}. The first message on this account loads the whole history, about ${tokens.toLocaleString('en')} tokens at once; compacting first (/compact) loads less.`,
    switchCopy: 'The conversation goes on in a copy on that account; this account keeps it as it is.',
    switchCost: (tokens: null | number) =>
      tokens === null
        ? "The first message reads the whole conversation again, on that account's plan."
        : `The first message reads the whole conversation again, about ${tokens.toLocaleString('en')} tokens at once, on that account's plan. Compacting first (/compact) reads less.`,
    switchGo: 'Switch',
    switchMissing: (names: string) => `That account does not have: ${names}.`,
    switchTitle: (name: string) => `Go on on ${name}?`,
    switchUnknown: 'What that account has could not be compared.',
    title: 'Accounts'
  },
  route: {
    add: (name: string) => `Add ${name} to the route`,
    close: 'Close',
    down: (name: string) => `${name} later`,
    empty: "No destination yet: at a plan limit, the executor's own accounts take over.",
    enabled: 'At a plan limit, follow this route',
    main: 'main account',
    note: 'At a plan limit the session goes on to the next destination logged in and not at its own limit, in this order. On another account of the same harness it goes on in a copy; on another harness a new session starts there with what this one did.',
    notInstalled: 'not installed',
    pill: (count: number) => (count ? `Route · ${String(count)}` : 'Route'),
    pillOn: (account: string, count: number) => (count ? `${account} · route ${String(count)}` : account),
    remove: (name: string) => `Take ${name} out of the route`,
    title: "The session's route: where it goes on at a plan limit",
    titleOn: "The session's account, and where it goes on at a plan limit",
    up: (name: string) => `${name} sooner`,
    wentOn: (name: string) => `The plan limit was reached: it went on on ${name}.`
  },
  executorPanel: {
    active: (on: number, all: number) => (on === all ? String(all) : `${String(on)} of ${String(all)}`),
    empty: 'No MCP servers.',
    failed: (error: string) => `They could not be read: ${error}`,
    hideInactive: 'Hide the ones down',
    loginThere: (title: string) => `An MCP server's login is done in ${title} itself (/mcp).`,
    mcp: 'MCP servers',
    moreSkills: (count: number) => `${String(count)} more`,
    noneActive: 'None connected.',
    none: (title: string) => `${title} does not say which MCP servers it has.`,
    plugins: 'Plugins',
    pluginSkills: (count: number) => (count === 1 ? '1 skill' : `${String(count)} skills`),
    pluginServers: (on: number, all: number) =>
      `${String(on)} of ${String(all)} ${all === 1 ? 'MCP server' : 'MCP servers'}`,
    showInactive: (count: number) =>
      count === 1
        ? 'Show 1 down (needs login, failed or off)'
        : `Show ${String(count)} down (need login, failed or off)`,
    skills: 'Skills',
    skillsEmpty: 'No skills.',
    skillsFolder: (folder: string) => `Read in ${folder}: the project's are here.`,
    skillsReading: 'Reading its skills…',
    origins: {
      account: 'From the account',
      local: 'This folder only',
      managed: 'From the organization',
      plugin: 'From plugins',
      project: 'From the project',
      user: 'Yours'
    } as Record<string, string>,
    outsideProjects: "Read outside any project: a folder's .mcp.json is not here.",
    reading: 'Reading its MCP servers…',
    refresh: 'Read again',
    statuses: {
      connected: 'connected',
      disabled: 'off',
      failed: 'failed',
      'needs-auth': 'needs login',
      pending: 'starting'
    } as Record<string, string>,
    tools: (count: number) => (count === 1 ? '1 tool' : `${String(count)} tools`),
    usage: 'Plan usage',
    usageExtra:
      'Extra usage is on: past these limits it goes on and bills, so it does not stop for the next account or route.',
    usageNone: 'This login has no plan limits: an API key or a cloud provider pays.',
    usageReading: 'Reading the plan usage…',
    used: (percent: number) => `${String(percent)}% used`,
    resets: (when: string) => `Resets in ${when}`,
    windows: {
      days: (count: number) => `${String(count)}-day limit`,
      session: 'Current session',
      week: 'Current week (all models)',
      weekOf: (model: string) => `Current week (${model})`
    }
  },
  status: {
    failed: 'stopped with an error',
    unread: 'done, not seen yet',
    waiting: 'waiting for you',
    working: 'working'
  },
  layout: {
    hidePanel: 'Hide the panel',
    hideSidebar: 'Hide the sidebar (Ctrl+B)',
    resizePanel: "The panel's width: drag, or double-click to reset",
    resizeSidebar: "The sidebar's width: drag, or double-click to reset",
    showPanel: 'Show the panel',
    showSidebar: 'Show the sidebar (Ctrl+B)'
  },
  updates: {
    apply: 'Update and reopen',
    brings: (count: number) =>
      count === 1
        ? 'One change on main that this Atlas lacks:'
        : `${String(count)} changes on main that this Atlas lacks:`,
    button: 'Update Atlas',
    closing: 'Closing to update…',
    dirty:
      'This installation has changes of its own, which the update refuses: commit or discard them in its folder first.',
    dismiss: 'Dismiss',
    done: (count: number) =>
      count === 1 ? 'Atlas was updated: one change.' : `Atlas was updated: ${String(count)} changes.`,
    executors: (count: number) =>
      count === 1 ? 'an executor session at work' : `${String(count)} executor sessions at work`,
    failed: (error: string) => `The update failed, and Atlas stayed as it was: ${error}`,
    how: 'Atlas closes, updates and opens again by itself, in about a minute.',
    later: 'Not now',
    log: 'See what it did',
    more: (count: number) => `and ${String(count)} more`,
    ok: 'OK',
    runningNow: 'Atlas is updating, and opens by itself when it is done.',
    stops: (what: string) => `Closing stops what runs now: ${what}.`,
    subagents: (count: number) => (count === 1 ? 'a subagent at work' : `${String(count)} subagents at work`),
    title: 'An update for Atlas',
    turns: (count: number) => (count === 1 ? 'a conversation answering' : `${String(count)} conversations answering`)
  },
  preview: {
    close: 'Close this preview',
    dismiss: 'Dismiss',
    notWeb: 'Only a web address opens here.',
    openBeside: 'Open beside',
    reading: 'Reading the file…'
  },
  terminal: {
    add: 'New terminal',
    cancel: 'Cancel',
    close: 'Close this terminal',
    dangerous: 'The trust gate finds this command dangerous. Run it anyway?',
    empty: 'No terminal open.',
    exit: (code: number) => `exit ${String(code)}`,
    name: 'Terminal',
    panel: 'Side panel',
    run: 'Run in the terminal',
    runAnyway: 'Run it',
    toggle: 'Show or hide the terminal (Ctrl+`)',
    unavailable: "Atlas's machine cannot open a terminal: it lacks the terminal's package (uv sync --all-extras there)."
  },
  thread: {
    compacted: 'The conversation was compacted.',

    empty: 'Ask Atlas anything.',
    failed: (error: string) => `The turn failed: ${error}`,
    halted: 'The turn stopped: the agent repeated the same call.',
    interrupted: 'Stopped.',
    limited: 'The turn stopped at a limit.',
    notice: 'Atlas was notified',
    /** A part for an event this app does not know: a newer server's. */
    other: 'not known to this version of the app',
    /** Under a `/name` that loaded a skill. */
    skill: 'skill loaded',
    steered: 'sent during the turn',
    summary: 'Summary of earlier messages',
    // The reasoning's closed line.
    thinking: 'Thinking…',
    thought: 'Thought',
    thoughtBriefly: 'Thought briefly',
    thoughtFor: (duration: string) => `Thought for ${duration}`,
    /** The button that scrolls a thread back down to its end. */
    toLatest: 'Scroll to the latest'
  },
  tool: {
    cancelled: 'cancelled',
    /** An output cut for display. */
    clamped: (omitted: string) => `… ${omitted} more characters truncated — use Copy all for the full output.`,
    copied: 'Copied',
    copyAll: 'Copy all',
    denied: 'denied',
    failed: 'failed',
    input: 'IN',
    output: 'OUT',
    running: 'running',
    spilled: (path: string) => `The whole result is in ${path}`,
    // Atlas's own tools get a title of their own, one at a time.
    titles: {
      read_file: { done: 'Read', pending: 'Reading' },
      run_command: { done: 'Ran', pending: 'Running' },
      web_search: { done: 'Searched', pending: 'Searching' }
    },
    waiting: 'waiting for its output…'
  },
  /** Settings, and their one section so far: the machines (step 15). */
  machines: {
    /** A notification's conversation on another machine than the one on screen. */
    onMachine: (conversation: string, machine: string) => `${conversation} (${machine})`,
    add: 'Add a machine',
    adding: 'Adding…',
    addTitle: 'Add a machine',
    alwaysOn: 'Always on',
    alwaysOnHint: 'Runs with the app closed and starts with the computer.',
    busy: 'A turn is running in this home; wait for it to end.',
    code: 'Connection code',
    codeCopied: 'Copied. It holds this home’s password: paste it only into your own app.',
    codeHint: 'Shown by “Open to my machines” on the other machine, or by `atlas server code` in its terminal.',
    copyCode: 'Copy the connection code',
    cancel: 'Cancel',
    close: 'Close',
    name: 'Name',
    namePlaceholder: 'Shop',
    newToken: 'Change the password',
    newTokenConfirm: 'Every code given before stops working. Continue?',
    newTokenDone: 'Password changed: give the new code to your machines.',
    noTailnet: 'Waiting for Tailscale:',
    open: 'Open to my machines',
    openHint: 'Also reachable from your other machines over Tailscale.',
    others: 'Other machines',
    othersEmpty: 'No other machine yet.',
    remove: 'Remove',
    removeConfirm: (name: string) => `Remove ${name}? Its Atlas is not touched: add it again any time.`,
    rename: 'Rename',
    reachable: 'Reachable',
    save: 'Save',
    selector: (machine: string) => `${machine}: switch machine`,
    settings: 'Settings',
    sshHost: 'SSH host',
    sshHostPlaceholder: 'user@host or a name from ~/.ssh/config',
    test: 'Test',
    testing: 'Testing…',
    thisComputer: 'This computer',
    thisMachine: (home: string) => `This machine · home ${home}`,
    title: 'Machines',
    transport: 'Reached by',
    viaSsh: 'SSH tunnel',
    viaTailscale: 'Tailscale',
    working: 'Working…'
  },
  /** A tool's time: 12ms, 4.2s, 25s, 1m 5s. */
  duration: (seconds: number) =>
    seconds < 1
      ? `${String(Math.max(1, Math.round(seconds * 1000)))}ms`
      : seconds < 60
        ? `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`
        : `${String(Math.floor(Math.round(seconds) / 60))}m ${String(Math.round(seconds) % 60)}s`,
  /** A running timer, in whole seconds: 4s, 1:05. */
  elapsed: (seconds: number) =>
    seconds < 60
      ? `${String(seconds)}s`
      : `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`
}

export type Messages = typeof en
