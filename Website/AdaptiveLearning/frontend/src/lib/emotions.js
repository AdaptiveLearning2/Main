/** FER+'s labels, colour and emoji: the one copy every page showing an emotion reads. */

// As the sidecar stores them; `emotions.test.js` pins this to `signal_fusion.FER_LABELS`.
export const FER_LABELS = ['neutral', 'happy', 'surprise', 'sad', 'angry', 'disgust', 'fear', 'contempt']

// Fixed per label, so a colour always means one emotion. `chart_render.py` mirrors it, pinned.
export const EMOTION_COLOURS = {
  neutral: '#94a3b8', happy: '#10b981', surprise: '#38bdf8',
  sad: '#6366f1', angry: '#f43f5e', disgust: '#84cc16',
  fear: '#a855f7', contempt: '#f59e0b',
}

// A slice with no entry, drawn in a colour no label uses.
export const UNKNOWN_EMOTION_COLOUR = '#cbd5e1'

const EMOTION_EMOJI = {
  neutral: '😐', happy: '😀', surprise: '😮', sad: '😢',
  angry: '😠', disgust: '🤢', fear: '😨', contempt: '😒',
}

/** The label's emoji, or null for one it has none for: a stand-in face would claim a mood. */
export function emotionEmoji(label) {
  return EMOTION_EMOJI[label] ?? null
}
