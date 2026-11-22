import { useEffect, useRef, useState } from "react";

export const SPEEDS = [1, 1.5, 2, 4];

export function SpeedButtons({ speed, onChange, label = "Playback speed" }: { speed: number; onChange: (speed: number) => void; label?: string }) {
  return (
    <div className="speed-row" role="group" aria-label={label}>
      {SPEEDS.map((rate) => (
        <button key={rate} type="button" className={speed === rate ? "active" : ""} aria-pressed={speed === rate} onClick={() => onChange(rate)}>
          {rate}x
        </button>
      ))}
    </div>
  );
}

export function VideoPlayer({ src, className, loop = false, groupSpeed }: { src: string; className?: string; loop?: boolean; groupSpeed?: number }) {
  const video = useRef<HTMLVideoElement | null>(null);
  const [speed, setSpeed] = useState(groupSpeed ?? 1);

  useEffect(() => {
    if (groupSpeed !== undefined) setSpeed(groupSpeed);
  }, [groupSpeed]);

  useEffect(() => {
    if (video.current) video.current.playbackRate = speed;
  }, [speed, src]);

  return (
    <div className="video-player">
      <video
        ref={video}
        className={className}
        controls
        muted
        loop={loop}
        playsInline
        preload="metadata"
        src={src}
        onLoadedMetadata={(event) => {
          event.currentTarget.playbackRate = speed;
        }}
      />
      <SpeedButtons speed={speed} onChange={setSpeed} />
    </div>
  );
}
