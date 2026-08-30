/** 浏览器端播放服务端下发的面试官 TTS（无 LiveTalking 时的降级路径）。 */

let current: HTMLAudioElement | null = null;
const queue: string[] = [];
let playing = false;

function revoke(url: string) {
  try {
    URL.revokeObjectURL(url);
  } catch {
    /* ignore */
  }
}

function drain() {
  if (playing) return;
  const next = queue.shift();
  if (!next) return;
  playing = true;
  const audio = new Audio(next);
  current = audio;
  audio.onended = () => {
    revoke(next);
    if (current === audio) current = null;
    playing = false;
    drain();
  };
  audio.onerror = () => {
    revoke(next);
    if (current === audio) current = null;
    playing = false;
    drain();
  };
  void audio.play().catch(() => {
    revoke(next);
    if (current === audio) current = null;
    playing = false;
    drain();
  });
}

export function stopAssistantAudio() {
  queue.splice(0).forEach(revoke);
  if (current) {
    current.pause();
    const src = current.src;
    current = null;
    revoke(src);
  }
  playing = false;
}

export function enqueueAssistantAudio(audioB64: string, interrupt = false) {
  const url = URL.createObjectURL(
    new Blob([Uint8Array.from(atob(audioB64), (c) => c.charCodeAt(0))], { type: "audio/wav" }),
  );
  if (interrupt) {
    stopAssistantAudio();
  }
  queue.push(url);
  drain();
}
