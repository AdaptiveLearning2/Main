import { Link } from 'react-router-dom'
import useAdminRead from '../../hooks/useAdminRead'
import { ReadState, Tile, Unread } from './adminUi'
import { reasonLabel } from './closeReasons'

const STATIONS = '/api/admin/stations'

function ago(seconds) {
  if (seconds == null) return 'unknown'
  if (seconds < 90) return `${Math.round(seconds)} s ago`
  return `${Math.round(seconds / 60)} min ago`
}

function clock(iso) {
  const t = iso ? new Date(iso) : null
  return t && !Number.isNaN(t.getTime()) ? `at ${t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''
}

// A holder's name, or why there is none: unreadable is not unnamed.
function who(person, namesRetrieved) {
  if (person.name) return person.name
  return namesRetrieved ? 'No name set' : 'Name unavailable'
}

// A poller still writing for a page that stopped asking: the abandoned-lesson case.
const unwatched = p => p.running && !p.page_watching

function PushNote({ data }) {
  if (data.ingest_mode !== 'push') return null
  return (
    <p className="text-xs text-gray-600 dark:text-gray-400">
      This deployment uses push ingestion: each student&rsquo;s own computer sends its headband&rsquo;s readings, so
      stations are not paired or polled here and this list stays empty.
    </p>
  )
}

function Station({ station, namesRetrieved, idleAfter }) {
  const { pairing, pollers } = station
  return (
    <li className="bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl px-4 py-3 space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono text-sm font-bold text-gray-900 dark:text-white">{station.device_id}</p>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          {station.refused_today} refused today
        </p>
      </div>
      {pairing ? (
        <p className="text-sm text-gray-900 dark:text-white">
          Held by {who(pairing, namesRetrieved)}, page last seen {ago(pairing.seen_seconds_ago)}
          {pairing.idle && (
            <span className="text-amber-800 dark:text-amber-300">
              {' '}— idle past {Math.round(idleAfter)} s, so the next student can take it
            </span>
          )}
        </p>
      ) : (
        <p className="text-sm text-gray-600 dark:text-gray-400">Not held.</p>
      )}
      {station.last_ended && (
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Last lesson here today ended {clock(station.last_ended.ended_at)}:{' '}
          {reasonLabel(station.last_ended.close_reason ?? 'unrecorded')}
        </p>
      )}
      {pollers.map(p => (
        <p key={p.session_id} className="text-xs text-gray-900 dark:text-white">
          Poller for {who(p, namesRetrieved)}: {p.recording ? 'recording' : 'paired, not recording'}
          {p.withheld && ', withheld by an admin switch'}, page polled {ago(p.page_seen_seconds_ago)}
          {unwatched(p) && (
            <span className="font-bold text-amber-800 dark:text-amber-300"> — page silent, poller still running</span>
          )}
        </p>
      ))}
    </li>
  )
}

function StationList({ data }) {
  if (!data.retrieved) {
    return <Unread what="Station pairings" note="A station may be held even though none is listed." />
  }
  return (
    <div className="space-y-3">
      <PushNote data={data} />
      {!data.refusals_retrieved && (
        <p className="text-xs text-amber-800 dark:text-amber-300">Today&rsquo;s refusal counts could not be read.</p>
      )}
      {data.last_ended_retrieved === false && (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          How each station&rsquo;s last lesson ended could not be read, so a released station may be missing.
        </p>
      )}
      {data.stations.length === 0
        ? <p className="text-sm text-gray-600 dark:text-gray-400">No station is held right now.</p>
        : (
          <ul className="space-y-2">
            {data.stations.map(s => (
              <Station key={s.device_id} station={s} namesRetrieved={data.names_retrieved}
                       idleAfter={data.idle_after_seconds} />
            ))}
          </ul>
        )}
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Pollers are the ones {data.poller_scope} runs. A page that stops polling releases its station after{' '}
        {Math.round(data.idle_after_seconds)} s.
      </p>
    </div>
  )
}

/** Overview's card: stations held, and pollers whose page has gone quiet. */
export function StationHolds() {
  const res = useAdminRead(STATIONS, 30_000)
  return (
    <div className="space-y-2">
      <ReadState res={res} what="station holds">
        {data => {
          if (!data.retrieved) return <Unread what="Station pairings" />
          const held = data.stations.filter(s => s.pairing).length
          const silent = data.stations.flatMap(s => s.pollers).filter(unwatched).length
          const refused = data.stations.reduce((n, s) => n + s.refused_today, 0)
          return (
            <>
              <PushNote data={data} />
              <div className="grid grid-cols-3 gap-3">
                <Tile label="Stations held" value={held} />
                <Tile label="Pollers with a silent page" value={silent} />
                <Tile label="Refused today" value={data.refusals_retrieved ? refused : '—'} />
              </div>
            </>
          )
        }}
      </ReadState>
      <Link to="/admin/stations" className="text-xs font-semibold text-slate-700 dark:text-slate-300 underline">
        Stations in detail
      </Link>
    </div>
  )
}

export default function AdminStations() {
  const res = useAdminRead(STATIONS, 5_000)
  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <header>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Stations</h1>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Who holds each headband station and whether their page is still there. No readings.
        </p>
      </header>
      <ReadState res={res} what="stations">{data => <StationList data={data} />}</ReadState>
    </div>
  )
}
