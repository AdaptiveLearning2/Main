import { useState } from 'react'
import { Download } from 'lucide-react'
import LoadError from '../../components/ui/LoadError'
import useAdminRead from '../../hooks/useAdminRead'
import { apiFetch } from '../../lib/api'
import { ReadState, Tile, Unread } from './adminUi'

const KIT = '/api/admin/kit'
const LINK = '/api/admin/kit/download-link'
const VERSIONS = '/api/admin/kit-versions'

// A navigation, not a fetch: the browser saves the file, and no connect-src is needed for the gate.
const goTo = url => window.location.assign(url)

const megabytes = bytes => `${Math.round(bytes / 1_000_000)} MB`
const day = iso => new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

function DownloadButton({ sha256, navigate }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // The hash shown: the backend links only to that installer, so the bytes match what the page says.
  const start = () => {
    setBusy(true)
    setError(null)
    return apiFetch(LINK, { method: 'POST', body: { sha256 } })
      .then(({ url }) => navigate(url))
      .catch(setError)
      .finally(() => setBusy(false))
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={start}
        disabled={busy}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-60 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white"
      >
        <Download size={16} /> {busy ? 'Getting a link…' : 'Download the installer'}
      </button>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        The link works for 10 minutes. The installer carries this site&rsquo;s learner token and download key, so keep it
        to the school&rsquo;s computers.
      </p>
      {/* 409: the installer changed or the gate is set up wrong, and the backend's sentence says which. */}
      {error?.status === 409
        ? <p className="text-sm text-amber-800 dark:text-amber-300">{error.message}</p>
        : error && <LoadError error={error} what="a download link" />}
    </div>
  )
}

function Offered({ data, navigate }) {
  if (!data.configured) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40">
        <p className="font-bold text-amber-900 dark:text-amber-200">This server has no download gate set up.</p>
        <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">
          Set KIT_GATE_URL and KIT_LINK_SECRET on the backend; DEVELOPER_SETUP_WINDOWS.md says how.
        </p>
      </div>
    )
  }
  if (data.problem) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40">
        <p className="font-bold text-amber-900 dark:text-amber-200">
          {data.problem_is === 'publish' ? 'The last publish did not finish.' : 'The download gate is set up wrong.'}
        </p>
        <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">{data.problem}. Fix it, then reload this page.</p>
      </div>
    )
  }
  if (!data.published) {
    return (
      <p className="text-sm text-gray-600 dark:text-gray-400">
        No installer has been published yet. Publish one with <code>publish_kit_update.ps1 -Setup</code>.
      </p>
    )
  }
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Tile label="Version" value={data.version} />
        <Tile label="Size" value={megabytes(data.size)} />
        <Tile label="Published" value={day(data.published_at)} />
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-600 dark:text-gray-400">SHA-256 of {data.file}</p>
        <code className="block break-all text-xs text-gray-900 dark:text-white">{data.sha256}</code>
      </div>
      <DownloadButton sha256={data.sha256} navigate={navigate} />
      <div className="space-y-2 text-sm text-gray-700 dark:text-gray-300">
        <p>
          On a student computer: run it as an administrator, choose <strong>Install for all users</strong>, and leave
          <strong> Keep the sensors up to date</strong> ticked.
        </p>
        <p>
          For school IT, silently and with updates left to IT:{' '}
          <code className="break-all">{data.file} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /MERGETASKS=&quot;!autoupdate&quot;</code>
        </p>
      </div>
    </div>
  )
}

// Newest first, by number: "0.10.0" is after "0.9.0".
const byVersion = (a, b) => {
  const [x, y] = [a, b].map(v => v.split('.').map(Number))
  return y[0] - x[0] || y[1] - x[1] || y[2] - x[2]
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

/** Students by the newest kit their headband lessons reported: the page's claim, so it is shown and gates nothing. */
function InUse({ data }) {
  if (!data.retrieved) return <Unread what="Kit versions" />
  const { students_by_version: byStudents, lessons_unreported: unreported, students_unreported: onlyUnreported } =
    data.versions
  const rows = Object.entries(byStudents || {}).sort(([a], [b]) => byVersion(a, b))
  return (
    <div className="space-y-2">
      {rows.length === 0
        ? <p className="text-sm text-gray-600 dark:text-gray-400">
            No headband lesson reported a kit version in the last {data.days} days.
          </p>
        : (
          <ul className="text-sm space-y-1">
            {rows.map(([version, n]) => (
              <li key={version} className="text-gray-900 dark:text-white">
                <span className="tabular-nums font-bold">{plural(n, 'student', 'students')}</span> on {version}
              </li>
            ))}
          </ul>
        )}
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Each student counts once, by the newest version their headband lessons reported in the last {data.days} days.
        {unreported > 0 && ` Also ${plural(unreported, 'headband lesson', 'headband lessons')} reported no version `
          + `(${plural(onlyUnreported, 'student', 'students')} with no other): an older kit, or a sidecar outside one.`}
      </p>
    </div>
  )
}

/** The student kit's installer, through a short link the backend signs: the file itself is never public. */
export default function AdminKit({ navigate = goTo }) {
  const res = useAdminRead(KIT)
  const versions = useAdminRead(VERSIONS)
  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <header>
        <h1 className="text-2xl font-black text-gray-900 dark:text-white">Sensors kit</h1>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          The installer for the headband and camera sensors on a student computer.
        </p>
      </header>
      <ReadState res={res} what="the kit installer">{data => <Offered data={data} navigate={navigate} />}</ReadState>
      <section className="space-y-2">
        <h2 className="text-xs font-black uppercase tracking-wide text-gray-600 dark:text-gray-400">Kits in use</h2>
        <ReadState res={versions} what="kit versions">{data => <InUse data={data} />}</ReadState>
      </section>
    </div>
  )
}
