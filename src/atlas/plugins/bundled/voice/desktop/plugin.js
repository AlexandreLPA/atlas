/**
 * The voice plugin's screen (step 17a): two buttons in the conversation's box.
 *
 * - **The microphone dictates**: a click records, a second click stops, the plugin's Python has
 *   OpenAI write it down (`dictate`), and the words land in the box, to be read before sending.
 * - **The live conversation**: OpenAI's full-duplex voice model (`gpt-live-1`) talks with the user
 *   over WebRTC from this app; the plugin's Python opens the session, so the key never reaches the
 *   app (`live_session`). The voice has no tools: each request it hands over (a delegation) is sent
 *   to the conversation as the box sends a message, marked as spoken for the model (`live_turn`, a
 *   note the Python's hook adds), and what Atlas answers is given back to the voice to say. It
 *   runs on a model of its own when the plugin's setting says so, the conversation's model put
 *   back at the end. It ends on the button, on a spoken "stop", or after a silence, which OpenAI
 *   bills too.
 *
 * Plain ESM, loaded by the app as it is: only `@atlas/plugin-sdk` and `react` are imported.
 */
import { host, useValue } from '@atlas/plugin-sdk'
import { createElement as h, useEffect, useState } from 'react'

const PORTUGUESE = (globalThis.navigator?.language ?? '').toLowerCase().startsWith('pt')

const WORDS = PORTUGUESE
  ? {
      dictate: 'Ditar: clique para gravar, clique de novo para escrever na caixa',
      recording: 'Gravando: clique para parar',
      transcribing: 'Escrevendo o que você disse…',
      talk: 'Conversar por voz',
      starting: 'Abrindo a conversa por voz…',
      listening: 'Ouvindo: clique para encerrar',
      thinking: 'O Atlas está trabalhando',
      speaking: 'Falando',
      ended: 'A conversa por voz terminou',
      quiet: 'A conversa por voz fechou depois do silêncio, que também é cobrado.',
      noKey: 'A voz precisa do OPENAI_API_KEY no cofre.',
      working: name => `O Atlas está trabalhando (${name}). Ainda não terminou.`,
      noAnswer: 'O Atlas terminou sem uma resposta para dizer.'
    }
  : {
      dictate: 'Dictate: click to record, click again to write it in the box',
      recording: 'Recording: click to stop',
      transcribing: 'Writing down what you said…',
      talk: 'Talk by voice',
      starting: 'Opening the voice conversation…',
      listening: 'Listening: click to end',
      thinking: 'Atlas is working',
      speaking: 'Speaking',
      ended: 'The voice conversation ended',
      quiet: 'The voice conversation closed after the silence, which is billed too.',
      noKey: 'Voice needs the OPENAI_API_KEY in the vault.',
      working: name => `Atlas is working (${name}). Not done yet.`,
      noAnswer: 'Atlas finished without an answer to say.'
    }

/** What ends the conversation when said alone. */
const STOP_PHRASES = new Set([
  'stop',
  'stop it',
  'end',
  'goodbye',
  'bye',
  'pare',
  'para',
  'parar',
  'chega',
  'encerrar',
  'encerra',
  'tchau',
  'desliga'
])
/** How long the user's words rest before they are judged as a whole ("stop", or more). */
const SETTLE_MS = 1500
/** The voice model takes at most about 500 tokens an append. */
const APPEND_CHARACTERS = 1400
/** How much of the spoken exchange a delegation carries. */
const CONTEXT_FRAGMENTS = 60

/** Whether `said` is only a stop phrase. */
export function isStopPhrase(said) {
  const words = said
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z ]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  return STOP_PHRASES.has(words)
}

/** A delegation's turn from the spoken exchange: `prompt`, what the user said last (the
 *  conversation's user message), and `spoken`, the exchange, for the model's note. */
export function delegated(fragments) {
  const turns = []

  for (const fragment of fragments.slice(-CONTEXT_FRAGMENTS)) {
    const last = turns.at(-1)

    if (last && last.who === fragment.who) {
      last.text += fragment.text
    } else {
      turns.push({ text: fragment.text, who: fragment.who })
    }
  }

  const tidy = text => text.replace(/\s+/g, ' ').trim()
  const lastUser = [...turns].reverse().find(turn => turn.who === 'user')
  const spoken = turns
    .map(turn => `${turn.who === 'user' ? 'User' : 'Voice'}: ${tidy(turn.text)}`)
    .filter(line => !line.endsWith(': '))
    .join('\n')

  return { prompt: tidy(lastUser?.text ?? '') || spoken.slice(-400), spoken }
}

/** Atlas's answer as words to say: no Markdown, no code, no addresses. */
export function speakable(text) {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/^\s{0,3}(#{1,6}|[-*+]|\d+[.)])\s+/gm, '')
    .replace(/[*_~>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** A text in appends the voice model takes, cut between sentences. */
export function appends(text, most = APPEND_CHARACTERS) {
  const parts = []
  let current = ''

  for (const sentence of text.split(/(?<=[.!?])\s+/)) {
    for (let start = 0; start < sentence.length; start += most) {
      const piece = sentence.slice(start, start + most)
      const joined = current ? `${current} ${piece}` : piece

      if (joined.length > most) {
        parts.push(current)
        current = piece
      } else {
        current = joined
      }
    }
  }

  if (current) {
    parts.push(current)
  }

  return parts.filter(Boolean)
}

/**
 * One live conversation, apart from WebRTC: `link` talks to the voice model, `atlas` to the
 * conversation. Each is a few functions, so the engine is tested without a microphone.
 *
 *   link:  open(history) -> Promise, send(event), close()
 *   atlas: conversation() -> id | null, send(text), mark(conversation, spoken),
 *          useVoiceModel(conversation) -> Promise, putModelBack() -> Promise
 */
export class LiveTalk {
  constructor({ atlas, link, now = () => Date.now(), onState = () => {}, quietSeconds = 120 }) {
    this.atlas = atlas
    this.link = link
    this.now = now
    this.onState = onState
    this.quietMs = quietSeconds * 1000
    this.state = 'idle'
    this.fragments = []
    this.utterance = ''
    this.settleTimer = null
    this.delegation = null
    this.speakingUntil = 0
    this.lastActive = now()
    this.ended = null
  }

  async start(history) {
    this.set('starting')
    await this.link.open(history)
    this.lastActive = this.now()
    this.set('listening')
  }

  set(state) {
    if (this.state !== state) {
      this.state = state
      this.onState(state)
    }
  }

  /** An event of the voice model, from the data channel. */
  heard(event) {
    if (this.ended) {
      return
    }

    switch (event.type) {
      case 'session.input_transcript.delta':
      case 'session.output_transcript.delta': {
        const who = event.type === 'session.input_transcript.delta' ? 'user' : 'voice'
        this.fragments.push({ text: event.delta ?? '', who })

        if (this.fragments.length > 400) {
          this.fragments.splice(0, 100)
        }

        this.lastActive = this.now()

        if (who === 'voice') {
          this.speakingUntil = this.now() + 1200
          this.set('speaking')
        } else {
          this.listenFor(event.delta ?? '')
        }

        return
      }

      case 'session.delegation.created':
        this.delegate(event.delegation?.id ?? null)

        return

      case 'session.closed':
        this.finish(event.reason ?? 'closed')

        return

      default:
        return
    }
  }

  /** The user's words, judged once they rest: a stop phrase alone ends the conversation. */
  listenFor(delta) {
    this.utterance += delta
    clearTimeout(this.settleTimer)
    this.settleTimer = setTimeout(() => {
      const said = this.utterance
      this.utterance = ''

      if (isStopPhrase(said)) {
        void this.end('stop_phrase')
      }
    }, SETTLE_MS)
  }

  delegate(id) {
    if (!id) {
      return
    }

    const { prompt, spoken } = delegated(this.fragments)

    if (isStopPhrase(prompt)) {
      void this.end('stop_phrase')

      return
    }

    this.delegation = id
    this.lastActive = this.now()
    this.set('thinking')
    const conversation = this.atlas.conversation()

    void (async () => {
      try {
        await this.atlas.useVoiceModel(conversation)
        await this.atlas.mark(conversation, spoken)
        await this.atlas.send(prompt)
      } catch (error) {
        this.say(id, `I could not reach Atlas: ${error instanceof Error ? error.message : String(error)}`)
        this.delegation = null
        this.set('listening')
      }
    })()
  }

  /** An event of the conversation, from Atlas: tools at work, and the answer. */
  answered(event) {
    if (this.ended || this.delegation === null) {
      return
    }

    if (event.type === 'tool.start') {
      this.think(this.delegation, WORDS.working(event.payload?.name ?? 'a tool'))

      return
    }

    if (event.type !== 'message.complete') {
      return
    }

    const id = this.delegation
    this.delegation = null
    this.lastActive = this.now()
    const text = event.payload?.status === 'answered' ? speakable(event.payload?.text ?? '') : ''

    if (text) {
      this.say(id, text)
    } else {
      this.think(id, WORDS.noAnswer)
    }

    this.set('listening')
  }

  say(id, text) {
    for (const part of appends(text)) {
      this.link.send({ content: part, delegation_id: id, type: 'session.commentary.append' })
    }
  }

  think(id, text) {
    const content = text.slice(0, APPEND_CHARACTERS)

    if (content) {
      this.link.send({ content, delegation_id: id, type: 'session.thinking.append' })
    }
  }

  /** Called every few seconds: the speaking state settles, and a long silence ends the call. */
  tick() {
    if (this.ended) {
      return
    }

    if (this.state === 'speaking' && this.now() > this.speakingUntil) {
      this.set(this.delegation ? 'thinking' : 'listening')
    }

    if (this.quietMs > 0 && this.delegation === null && this.now() - this.lastActive > this.quietMs) {
      void this.end('quiet')
    }
  }

  async end(reason = 'ended') {
    if (this.ended) {
      return
    }

    this.link.send({ type: 'session.close' })
    this.finish(reason)
  }

  finish(reason) {
    if (this.ended) {
      return
    }

    this.ended = reason
    clearTimeout(this.settleTimer)
    this.link.close()
    this.set('idle')
    void this.atlas.putModelBack().catch(() => undefined)
  }
}

/** The WebRTC side: the microphone out, the voice in, the events both ways. */
class WebRtcLink {
  constructor(ctx, onEvent, onClosed) {
    this.ctx = ctx
    this.onEvent = onEvent
    this.onClosed = onClosed
    this.peer = null
    this.channel = null
    this.microphone = null
    this.audio = new Audio()
    this.audio.autoplay = true
  }

  async open(history) {
    const peer = new RTCPeerConnection()
    this.peer = peer
    peer.addEventListener('track', event => {
      this.audio.srcObject = new MediaStream([event.track])
      void this.audio.play().catch(() => undefined)
    })
    peer.addEventListener('connectionstatechange', () => {
      if (peer.connectionState === 'failed' || peer.connectionState === 'disconnected') {
        this.onClosed('connection_lost')
      }
    })
    this.microphone = await navigator.mediaDevices.getUserMedia({
      audio: { autoGainControl: true, echoCancellation: true, noiseSuppression: true }
    })

    for (const track of this.microphone.getAudioTracks()) {
      peer.addTrack(track, this.microphone)
    }

    // The events' channel is made before the offer, so the offer carries it.
    const channel = peer.createDataChannel('oai-events')
    this.channel = channel
    channel.addEventListener('message', ({ data }) => {
      try {
        this.onEvent(JSON.parse(String(data)))
      } catch {
        // Not JSON: nothing the conversation reads.
      }
    })
    channel.addEventListener('close', () => {
      this.onClosed('connection_lost')
    })
    await peer.setLocalDescription(await peer.createOffer())
    await gathered(peer)
    const answer = await this.ctx.call('live_session', { history, sdp: peer.localDescription?.sdp ?? '' })
    await peer.setRemoteDescription({ sdp: answer.sdp, type: 'answer' })
  }

  send(event) {
    if (this.channel?.readyState === 'open') {
      this.channel.send(JSON.stringify(event))
    }
  }

  close() {
    this.microphone?.getTracks().forEach(track => track.stop())
    this.channel?.close()
    this.peer?.close()
    this.audio.srcObject = null
  }
}

/** The offer's candidates gathered, or 5 s gone: the voice model answers with those it has. */
function gathered(peer) {
  if (peer.iceGatheringState === 'complete') {
    return Promise.resolve()
  }

  return new Promise(resolve => {
    const timer = setTimeout(resolve, 5000)
    peer.addEventListener('icegatheringstatechange', () => {
      if (peer.iceGatheringState === 'complete') {
        clearTimeout(timer)
        resolve()
      }
    })
  })
}

/** The conversation, as the live engine reaches it through the app and the plugin's Python. */
function atlasFor(ctx, turns) {
  let kept = null

  return {
    conversation: () => host.state.conversation.get(),
    mark: (conversation, spoken) => ctx.call('live_turn', { conversation: conversation ?? '', spoken }),
    putModelBack: async () => {
      const back = kept ?? ctx.storage.get('model_to_put_back', null)
      kept = null
      ctx.storage.remove('model_to_put_back')

      if (back?.conversation) {
        await host.request('conversation.model', {
          conversation_id: back.conversation,
          effort: back.effort,
          model: back.model,
          provider: back.provider
        })
      }
    },
    send: text => host.send(text),
    useVoiceModel: async conversation => {
      if (!turns || !conversation || kept?.conversation === conversation) {
        return
      }

      const now = await host.request('conversation.model', { conversation_id: conversation })

      if (now.provider && now.model) {
        // Kept in storage too, so a closed window puts it back at the next start.
        kept = { conversation, effort: now.effort, model: now.model, provider: now.provider }
        ctx.storage.set('model_to_put_back', kept)
      }

      try {
        await host.request('conversation.model', { conversation_id: conversation, ...turns })
      } catch {
        // A model this home cannot use: the conversation's own answers.
        kept = null
        ctx.storage.remove('model_to_put_back')
      }
    }
  }
}

/** Its last messages, for the voice to know what was said before. */
async function historyOf(conversation) {
  if (!conversation) {
    return []
  }

  try {
    const opened = await host.request('conversation.open', { conversation_id: conversation })

    return opened.messages
      .filter(message => message.role === 'user' || message.role === 'assistant')
      .map(message => ({ role: message.role, text: message.text }))
      .slice(-24)
  } catch {
    return []
  }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(reader.error ?? new Error('The recording could not be read.'))
    reader.readAsDataURL(blob)
  })
}

const BUTTON = 'grid h-11 w-11 shrink-0 place-items-center rounded-lg border text-lg disabled:opacity-40 '
const QUIET =
  'border-neutral-300 text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800'
const LIVE = 'border-rose-500 bg-rose-500 text-white'

function DictateButton({ ctx }) {
  const [state, setState] = useState('idle')
  const [error, setError] = useState('')
  const [recorder, setRecorder] = useState(null)

  const start = async () => {
    setError('')

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const made = new MediaRecorder(stream)
      const chunks = []
      made.addEventListener('dataavailable', event => {
        if (event.data.size) {
          chunks.push(event.data)
        }
      })
      made.addEventListener('stop', async () => {
        stream.getTracks().forEach(track => track.stop())
        setState('transcribing')

        try {
          const blob = new Blob(chunks, { type: made.mimeType || 'audio/webm' })
          const written = await ctx.call('dictate', { audio: await blobToBase64(blob), media_type: blob.type })
          host.insertText(written.text ?? '')
        } catch (failed) {
          setError(failed instanceof Error ? failed.message : String(failed))
        } finally {
          setState('idle')
          setRecorder(null)
        }
      })
      made.start()
      setRecorder(made)
      setState('recording')
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : String(failed))
      setState('idle')
    }
  }

  const label = state === 'recording' ? WORDS.recording : state === 'transcribing' ? WORDS.transcribing : WORDS.dictate

  return h(
    'button',
    {
      'aria-label': label,
      'aria-pressed': state === 'recording',
      className: BUTTON + (state === 'recording' ? LIVE : error ? 'border-red-500 text-red-600' : QUIET),
      'data-state': state,
      'data-testid': 'voice-dictate',
      disabled: state === 'transcribing',
      onClick: () => (state === 'recording' ? recorder?.stop() : void start()),
      title: error ? `${label}\n${error}` : label,
      type: 'button'
    },
    state === 'transcribing' ? '…' : '🎤'
  )
}

function TalkButton({ ctx }) {
  const [state, setState] = useState('idle')
  const [talk, setTalk] = useState(null)
  const [error, setError] = useState('')
  const connection = useValue(host.state.connection)

  useEffect(() => {
    if (!talk) {
      return undefined
    }

    const stops = [
      ctx.onEvent('tool.start', event => {
        if (event.conversation_id === host.state.conversation.get()) talk.answered(event)
      }),
      ctx.onEvent('message.complete', event => {
        if (event.conversation_id === host.state.conversation.get()) talk.answered(event)
      }),
      ctx.setInterval(() => talk.tick(), 2000)
    ]

    return () => stops.forEach(stop => stop())
  }, [ctx, talk])

  const start = async () => {
    setError('')
    const status = await ctx.call('live_status')

    if (!status.ready) {
      setError(status.why || WORDS.noKey)

      return
    }

    let engine = null
    const link = new WebRtcLink(
      ctx,
      event => engine?.heard(event),
      reason => engine?.finish(reason)
    )
    engine = new LiveTalk({
      atlas: atlasFor(ctx, status.turns),
      link,
      onState: next => {
        setState(next)

        if (next === 'idle') {
          setTalk(null)

          if (engine?.ended === 'quiet') {
            setError(WORDS.quiet)
          }
        }
      },
      quietSeconds: status.quiet_seconds
    })
    setTalk(engine)

    try {
      await engine.start(await historyOf(host.state.conversation.get()))
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : String(failed))
      engine.finish('failed')
    }
  }

  const label =
    {
      idle: WORDS.talk,
      listening: WORDS.listening,
      speaking: WORDS.speaking,
      starting: WORDS.starting,
      thinking: WORDS.thinking
    }[state] ?? WORDS.talk

  return h(
    'button',
    {
      'aria-label': label,
      'aria-pressed': state !== 'idle',
      className: BUTTON + (state !== 'idle' ? LIVE : error ? 'border-red-500 text-red-600' : QUIET),
      'data-state': state,
      'data-testid': 'voice-talk',
      disabled: connection.state !== 'open' || state === 'starting',
      onClick: () => (talk ? void talk.end('button') : void start()),
      title: error ? `${label}\n${error}` : label,
      type: 'button'
    },
    state === 'thinking' ? '⏳' : state === 'speaking' ? '🔊' : '🎧'
  )
}

export default {
  id: 'voice',
  register(ctx) {
    // A conversation whose model the voice changed before the window closed gets it back.
    void atlasFor(ctx, null)
      .putModelBack()
      .catch(() => undefined)
    ctx.register({
      area: 'box',
      id: 'dictate',
      order: 10,
      render: () => h(DictateButton, { ctx }),
      title: WORDS.dictate
    })
    ctx.register({ area: 'box', id: 'talk', order: 20, render: () => h(TalkButton, { ctx }), title: WORDS.talk })
  }
}
