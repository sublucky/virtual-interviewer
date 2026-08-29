import { useCallback, useEffect, useRef, useState } from "react";

/**
 * MVP 的 ASR：浏览器 Web Speech API（架构 §3.10）。
 * 只回传最终文本，服务端不接收音频流；换本地 Whisper 时替换本 hook 即可。
 */
export function useSpeech(onFinal: (text: string) => void) {
  const recognitionRef = useRef<any>(null);
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [partial, setPartial] = useState("");

  useEffect(() => {
    const Impl = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    if (!Impl) return;
    const recognition = new Impl();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) {
          const text = String(result[0].transcript).trim();
          if (text) onFinal(text);
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
  }, [onFinal]);

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
