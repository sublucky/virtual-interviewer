import { useCallback, useEffect, useRef, useState } from "react";

/**
 * MVP 的 ASR：浏览器 Web Speech API（架构 §3.10）。
 * 回调用 ref 固定，避免每轮答题重建识别器。
 */
export function useSpeech(onFinal: (text: string) => void) {
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const onFinalRef = useRef(onFinal);
  onFinalRef.current = onFinal;
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [partial, setPartial] = useState("");

  useEffect(() => {
    const Impl =
      (window as unknown as { SpeechRecognition?: new () => SpeechRecognition }).SpeechRecognition ??
      (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognition }).webkitSpeechRecognition;
    if (!Impl) return;
    const recognition = new Impl();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) {
          const text = String(result[0].transcript).trim();
          if (text) onFinalRef.current(text);
        } else {
          interim += result[0].transcript;
        }
      }
      setPartial(interim);
    };
    recognition.onend = () => {
      setListening(false);
      setPartial("");
    };
    recognition.onerror = () => setListening(false);

    recognitionRef.current = recognition;
    setSupported(true);
    return () => recognition.abort();
  }, []);

  const start = useCallback(() => {
    try {
      recognitionRef.current?.start();
      setListening(true);
    } catch {
      // 重复 start 会抛错，忽略
    }
  }, []);

  const stop = useCallback(() => recognitionRef.current?.stop(), []);

  return { supported, listening, partial, start, stop };
}

interface SpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

interface SpeechRecognitionEvent {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
}
