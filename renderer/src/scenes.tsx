import {measureText} from '@remotion/layout-utils';
import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {OBJECTS} from './objects';
import {SAFE, STAGE_W, Theme, W, fonts} from './theme';
import {Word} from './types';

// ------------------------------------------------------------------------------------ helpers

export const useSpring = (delay: number, damping = 14, durationInFrames?: number) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	return spring({frame: frame - delay, fps, config: {damping, mass: 0.7}, durationInFrames});
};

/** The frame (relative to the scene) when a word containing `token` is spoken, or `fallback`. */
export const wordFrame = (words: Word[], token: string | undefined, fallback: number) => {
	if (!token) return fallback;
	const needle = token.toLowerCase().replace(/[^a-z0-9%$]/g, '');
	if (!needle) return fallback;
	const hit = words.find((w) => w.w.toLowerCase().replace(/[^a-z0-9%$]/g, '').includes(needle));
	return hit ? hit.f0 : fallback;
};

/** Break text into lines that fit `maxWidth`, shrinking the font until it fits in `maxLines`. */
export const fitLines = (text: string, maxWidth: number, maxSize: number, maxLines: number, fontFamily: string, fontWeight: string) => {
	const words = text.split(/\s+/).filter(Boolean);
	for (let size = maxSize; size >= 34; size -= 4) {
		const lines: string[] = [];
		let current = '';
		let ok = true;
		for (const word of words) {
			const trial = current ? `${current} ${word}` : word;
			if (measureText({text: trial, fontFamily, fontSize: size, fontWeight}).width <= maxWidth) {
				current = trial;
			} else {
				if (!current) {
					ok = false;
					break;
				}
				lines.push(current);
				current = word;
			}
		}
		if (current) lines.push(current);
		if (ok && lines.length <= maxLines && lines.every((line) => measureText({text: line, fontFamily, fontSize: size, fontWeight}).width <= maxWidth)) {
			return {size, lines};
		}
	}
	return {size: 34, lines: [text]};
};

const Headline: React.FC<{
	text: string;
	th: Theme;
	maxSize?: number;
	maxLines?: number;
	width?: number;
	highlight?: string;
	delay?: number;
	align?: 'center' | 'left';
	color?: string;
	instant?: boolean;
}> = ({text, th, maxSize = 96, maxLines = 3, width = STAGE_W, highlight, delay = 0, align = 'center', color, instant = false}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {size, lines} = fitLines(text, width, maxSize, maxLines, fonts.display, '800');
	let index = 0;
	const mark = (highlight ?? '').toLowerCase().replace(/[^a-z0-9%$]/g, '');
	return (
		<div style={{width, textAlign: align, fontFamily: fonts.display, fontWeight: 800, fontSize: size, lineHeight: 1.08, color: color ?? th.ink, letterSpacing: -1}}>
			{lines.map((line, li) => (
				<div key={li} style={{whiteSpace: 'nowrap'}}>
					{line.split(' ').map((word, wi, all) => {
						const k = instant ? 1 : spring({frame: frame - delay - index++ * 2.5, fps, config: {damping: 15, mass: 0.6}});
						const hot = mark && word.toLowerCase().replace(/[^a-z0-9%$]/g, '').includes(mark);
						return (
							<span key={wi} style={{display: 'inline-block', transform: `translateY(${(1 - k) * 40}px)`, opacity: k, color: hot ? th.accent : undefined, marginRight: wi < all.length - 1 ? size * 0.24 : 0}}>
								{word}
							</span>
						);
					})}
				</div>
			))}
		</div>
	);
};

const Body: React.FC<{text: string; th: Theme; size?: number; width?: number; color?: string; weight?: number; align?: 'center' | 'left'}> = ({text, th, size = 46, width = STAGE_W, color, weight = 600, align = 'center'}) => {
	const fit = fitLines(text, width, size, 4, fonts.body, String(weight));
	return (
		<div style={{width, textAlign: align, fontFamily: fonts.body, fontWeight: weight, fontSize: fit.size, lineHeight: 1.25, color: color ?? th.inkSoft}}>
			{fit.lines.map((line, i) => (
				<div key={i}>{line}</div>
			))}
		</div>
	);
};

const Obj: React.FC<{name?: string; th: Theme; size: number; t: number; extra?: Record<string, any>; style?: React.CSSProperties}> = ({name, th, size, t, extra, style}) => {
	const Comp = OBJECTS[name ?? ''] ?? OBJECTS.lightbulb;
	const frame = useCurrentFrame();
	// Every object breathes: a slow float and sway, phase-shifted per object so pairs never move in step.
	const phase = (name ?? '').split('').reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 60;
	const bob = Math.sin(((frame + phase) / 46) * Math.PI) * size * 0.018;
	const sway = Math.sin(((frame + phase) / 70) * Math.PI) * 1.6;
	return (
		<div style={{display: 'inline-flex', transform: `translateY(${bob}px) rotate(${sway}deg)`}}>
			<Comp th={th} size={size} t={t} style={style} {...(extra ?? {})} />
		</div>
	);
};

const loop = (frame: number, period: number) => (frame % period) / period;

const Stage: React.FC<{children: React.ReactNode; justify?: 'center' | 'space-between' | 'flex-start'}> = ({children, justify = 'center'}) => (
	<AbsoluteFill style={{top: SAFE.stageTop, height: SAFE.stageBottom - SAFE.stageTop, left: SAFE.left, width: STAGE_W, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: justify, gap: 36}}>
		{children}
	</AbsoluteFill>
);

type TP = {p: Record<string, any>; th: Theme; words: Word[]; dur: number};

// ------------------------------------------------------------------------------------ templates

const Hook: React.FC<TP> = ({p, th, dur}) => {
	const frame = useCurrentFrame();
	// Readable on the very first frame: a scroll decision is made before any fade would finish.
	const punch = interpolate(frame, [0, 8], [1.06, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	const k = useSpring(4, 11);
	return (
		<Stage>
			<div style={{transform: `scale(${punch})`}}>
				<Headline text={p.headline ?? ''} th={th} maxSize={124} maxLines={4} highlight={p.highlight} instant />
			</div>
			<div style={{display: 'flex', gap: 50, alignItems: 'center', transform: `scale(${0.75 + 0.25 * k}) translateY(${(1 - k) * 60}px)`, opacity: 0.4 + 0.6 * k}}>
				<Obj name={p.object} th={th} size={p.object2 ? 360 : 480} t={loop(frame, Math.max(dur, 60))} />
				{p.object2 ? <Obj name={p.object2} th={th} size={360} t={loop(frame + 20, Math.max(dur, 60))} /> : null}
			</div>
		</Stage>
	);
};

const Question: React.FC<TP> = ({p, th}) => {
	const frame = useCurrentFrame();
	const k = useSpring(0, 18);
	const objects = useSpring(12, 14);
	return (
		<Stage>
			<div style={{position: 'absolute', fontFamily: fonts.display, fontWeight: 800, fontSize: 900, color: th.paperDeep, transform: `scale(${0.8 + 0.2 * k}) rotate(${(1 - k) * -10}deg)`, lineHeight: 1}}>?</div>
			<Headline text={p.question ?? ''} th={th} maxSize={110} maxLines={5} highlight={p.highlight} delay={4} />
			{p.object ? (
				<div style={{display: 'flex', gap: 60, alignItems: 'center', opacity: objects, transform: `translateY(${(1 - objects) * 50}px)`}}>
					<Obj name={p.object} th={th} size={p.object2 ? 300 : 380} t={loop(frame, 60)} />
					{p.object2 ? <Obj name={p.object2} th={th} size={300} t={loop(frame + 20, 60)} /> : null}
				</div>
			) : null}
		</Stage>
	);
};

const Versus: React.FC<TP> = ({p, th, words, dur}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const left = p.left ?? {};
	const right = p.right ?? {};
	const decide = wordFrame(words, p.winner_word, Math.round(dur * 0.55));
	const won = spring({frame: frame - decide, fps, config: {damping: 13}});
	const card = (side: any, i: number, isWinner: boolean, isLoser: boolean) => {
		const k = spring({frame: frame - 4 - i * 8, fps, config: {damping: 14}});
		return (
			<div
				style={{
					width: 390,
					height: 560,
					background: th.card,
					borderRadius: 36,
					border: `7px solid ${isWinner && won > 0.5 ? (p.winner_tone === 'bad' ? th.alert : th.accent) : th.ink}`,
					boxShadow: '0 18px 0 rgba(20,33,61,0.12)',
					display: 'flex',
					flexDirection: 'column',
					alignItems: 'center',
					justifyContent: 'center',
					gap: 18,
					transform: `translateX(${(1 - k) * (i ? 300 : -300)}px) scale(${1 + (isWinner ? 0.07 : isLoser ? -0.06 : 0) * won})`,
					opacity: k * (isLoser ? 1 - 0.45 * won : 1),
				}}
			>
				<Obj name={side.object} th={th} size={250} t={loop(frame + i * 15, 50)} />
				<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 64, color: th.ink}}>{side.label}</div>
				{side.note ? <Body text={side.note} th={th} size={38} width={330} /> : null}
			</div>
		);
	};
	const vs = useSpring(14, 10);
	return (
		<Stage>
			{p.title ? <Headline text={p.title} th={th} maxSize={84} maxLines={2} /> : null}
			<div style={{display: 'flex', gap: 34, alignItems: 'center', position: 'relative'}}>
				{card(left, 0, p.winner === 'left', p.winner === 'right')}
				{card(right, 1, p.winner === 'right', p.winner === 'left')}
				<div style={{position: 'absolute', left: '50%', top: '50%', width: 120, height: 120, marginLeft: -60, marginTop: -60, borderRadius: 60, background: th.gold, border: `7px solid ${th.ink}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: fonts.display, fontWeight: 800, fontSize: 46, color: th.ink, transform: `scale(${vs})`}}>VS</div>
			</div>
		</Stage>
	);
};

const Stat: React.FC<TP> = ({p, th, words, dur}) => {
	const frame = useCurrentFrame();
	const start = Math.max(0, wordFrame(words, p.value_word, 6) - 6);
	const value = Number(p.value ?? 0);
	const progress = interpolate(frame, [start, start + 26], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	const decimals = String(p.value ?? '').includes('.') ? String(p.value).split('.')[1].length : 0;
	const shown = (value * progress).toFixed(decimals);
	const k = useSpring(start, 12);
	const label = useSpring(start + 14, 16);
	const {size} = fitLines(`${p.prefix ?? ''}${p.value}${p.suffix ?? ''}`, STAGE_W, 280, 1, fonts.display, '800');
	return (
		<Stage>
			{p.object ? <Obj name={p.object} th={th} size={260} t={loop(frame, Math.max(dur, 60))} style={{opacity: label}} /> : null}
			<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: size, lineHeight: 1, color: p.tone === 'bad' ? th.alert : th.accent, transform: `scale(${0.7 + 0.3 * k})`, opacity: k, letterSpacing: -4}}>
				{p.prefix ?? ''}
				{shown}
				{p.suffix ?? ''}
			</div>
			<div style={{opacity: label, transform: `translateY(${(1 - label) * 30}px)`}}>
				<Headline text={p.label ?? ''} th={th} maxSize={72} maxLines={3} delay={start + 14} />
			</div>
		</Stage>
	);
};

const Bars: React.FC<TP> = ({p, th}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const bars: {label: string; value: number}[] = (p.bars ?? []).slice(0, 4);
	const top = Math.max(...bars.map((b) => Number(b.value) || 0), 1);
	const maxH = 560;
	return (
		<Stage>
			{p.title ? <Headline text={p.title} th={th} maxSize={80} maxLines={2} /> : null}
			<div style={{display: 'flex', alignItems: 'flex-end', gap: 50, height: maxH + 150, borderBottom: `7px solid ${th.ink}`, paddingBottom: 0}}>
				{bars.map((bar, i) => {
					const k = spring({frame: frame - 8 - i * 12, fps, config: {damping: 16}});
					const h = maxH * (Number(bar.value) / top) * k;
					const best = Number(bar.value) === top;
					return (
						<div key={i} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', width: 190}}>
							<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 58, color: th.ink, opacity: k}}>
								{p.prefix ?? ''}
								{bar.value}
								{p.unit ?? ''}
							</div>
							<div style={{width: 160, height: h, background: best ? (p.tone === 'bad' ? th.alert : th.accent) : th.line, border: `7px solid ${th.ink}`, borderBottom: 'none', borderRadius: '18px 18px 0 0', marginTop: 12}} />
						</div>
					);
				})}
			</div>
			<div style={{display: 'flex', gap: 50, marginTop: -10}}>
				{bars.map((bar, i) => (
					<div key={i} style={{width: 190, textAlign: 'center'}}>
						<Body text={bar.label} th={th} size={40} width={190} color={th.ink} weight={700} />
					</div>
				))}
			</div>
		</Stage>
	);
};

const Steps: React.FC<TP> = ({p, th, dur}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const steps: {text: string; object?: string}[] = (p.steps ?? []).slice(0, 4);
	const slot = (dur * 0.8) / Math.max(steps.length, 1);
	const big = steps.length <= 2;
	const icon = big ? 200 : 120;
	return (
		<Stage justify="center">
			{p.title ? <Headline text={p.title} th={th} maxSize={80} maxLines={2} /> : null}
			<div style={{display: 'flex', flexDirection: 'column', gap: 28, width: STAGE_W}}>
				{steps.map((step, i) => {
					const k = spring({frame: frame - 4 - i * slot, fps, config: {damping: 15}});
					return (
						<div key={i} style={{display: 'flex', alignItems: 'center', gap: 28, background: th.card, borderRadius: 34, border: `6px solid ${th.ink}`, boxShadow: '0 12px 0 rgba(20,33,61,0.10)', padding: big ? '34px 36px' : '18px 28px', opacity: k, transform: `translateX(${(1 - k) * -120}px)`}}>
							<div style={{width: icon, height: icon, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
								{step.object ? <Obj name={step.object} th={th} size={icon} t={loop(frame, 60)} /> : <div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: icon * 0.66, color: th.accent}}>{i + 1}</div>}
							</div>
							<Body text={step.text} th={th} size={big ? 64 : 50} width={STAGE_W - icon - 100} color={th.ink} weight={700} align="left" />
						</div>
					);
				})}
			</div>
		</Stage>
	);
};

const ObjectScene: React.FC<TP> = ({p, th, dur}) => {
	const frame = useCurrentFrame();
	const k = useSpring(0, 12);
	const t = p.loop === false ? Math.min(1, frame / Math.max(dur * 0.7, 1)) : loop(frame, 60);
	return (
		<Stage>
			{p.label ? <Headline text={p.label} th={th} maxSize={92} maxLines={3} highlight={p.highlight} /> : null}
			<div style={{transform: `scale(${0.5 + 0.5 * k}) rotate(${(1 - k) * -8}deg)`, opacity: k}}>
				<Obj name={p.object} th={th} size={560} t={t} extra={p.object === 'gauge' ? {value: Number(p.value ?? 0.5)} : p.object === 'brain' ? {glow: th.gold} : {}} />
			</div>
			{p.sublabel ? <Body text={p.sublabel} th={th} size={46} /> : null}
		</Stage>
	);
};

const Study: React.FC<TP> = ({p, th}) => {
	const k = useSpring(0, 14);
	const caveat = useSpring(30, 16);
	return (
		<Stage>
			<div style={{width: STAGE_W, background: th.card, borderRadius: 40, border: `7px solid ${th.ink}`, boxShadow: '0 20px 0 rgba(20,33,61,0.12)', padding: '50px 56px', display: 'flex', flexDirection: 'column', gap: 30, transform: `translateY(${(1 - k) * 120}px) rotate(${(1 - k) * 3}deg)`, opacity: k}}>
				<div style={{display: 'flex', alignItems: 'center', gap: 18}}>
					<div style={{background: th.ink, color: th.paper, fontFamily: fonts.body, fontWeight: 800, fontSize: 30, padding: '10px 22px', borderRadius: 999, letterSpacing: 2}}>STUDY</div>
					<div style={{fontFamily: fonts.body, fontWeight: 700, fontSize: 34, color: th.inkSoft}}>{p.source}</div>
				</div>
				<Headline text={p.finding ?? ''} th={th} maxSize={74} maxLines={5} width={STAGE_W - 112} align="left" highlight={p.highlight} delay={8} />
				{p.caveat ? (
					<div style={{display: 'flex', gap: 18, alignItems: 'flex-start', opacity: caveat, borderTop: `4px dashed ${th.line}`, paddingTop: 26}}>
						<div style={{flex: 'none', width: 52, height: 52, borderRadius: 26, background: th.gold, border: `5px solid ${th.ink}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: fonts.display, fontWeight: 800, fontSize: 32}}>!</div>
						<Body text={p.caveat} th={th} size={40} width={STAGE_W - 200} align="left" />
					</div>
				) : null}
			</div>
		</Stage>
	);
};

const BrainScene: React.FC<TP> = ({p, th, words, dur}) => {
	const frame = useCurrentFrame();
	const lightAt = wordFrame(words, p.light_word, Math.round(dur * 0.35));
	const glow = interpolate(frame, [lightAt, lightAt + 12], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
	const neutral = p.tone === 'neutral';
	const pulse = neutral ? 0 : glow * (0.75 + 0.25 * Math.sin(frame / 4));
	const k = useSpring(0, 13);
	return (
		<Stage>
			{p.label ? <Headline text={p.label} th={th} maxSize={88} maxLines={3} highlight={p.highlight} /> : null}
			<div style={{position: 'relative', transform: `scale(${0.6 + 0.4 * k})`, opacity: k}}>
				<Obj name="brain" th={th} size={540} t={pulse} extra={neutral ? {} : {glow: p.tone === 'pain' ? th.alert : th.gold}} />
				{p.tag ? (
					<div style={{position: 'absolute', right: -40, top: 40, background: neutral ? th.card : p.tone === 'pain' ? th.alert : th.gold, border: `6px solid ${th.ink}`, borderRadius: 24, padding: '12px 26px', fontFamily: fonts.display, fontWeight: 800, fontSize: 50, color: th.ink, opacity: neutral ? k : glow, transform: `scale(${0.8 + 0.2 * (neutral ? k : glow)})`}}>
						{p.tag}
					</div>
				) : null}
			</div>
			{p.sublabel ? <Body text={p.sublabel} th={th} size={44} /> : null}
		</Stage>
	);
};

const GaugeScene: React.FC<TP> = ({p, th, dur}) => {
	const frame = useCurrentFrame();
	const value = interpolate(frame, [8, Math.max(dur * 0.6, 20)], [Number(p.from ?? 0.1), Number(p.to ?? 0.9)], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)});
	return (
		<Stage>
			{p.label ? <Headline text={p.label} th={th} maxSize={88} maxLines={3} highlight={p.highlight} /> : null}
			<Obj name="gauge" th={th} size={600} t={0} extra={{value}} />
			<div style={{display: 'flex', justifyContent: 'space-between', width: 620, marginTop: -60}}>
				<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 48, color: th.accent}}>{p.low ?? 'Low'}</div>
				<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 48, color: th.alert}}>{p.high ?? 'High'}</div>
			</div>
		</Stage>
	);
};

const Takeaway: React.FC<TP> = ({p, th}) => {
	const frame = useCurrentFrame();
	const k = useSpring(0, 14);
	const check = useSpring(18, 10);
	return (
		<Stage>
			<div style={{width: 200, height: 200, borderRadius: 100, background: p.mark === 'question' ? th.gold : th.accent, border: `8px solid ${th.ink}`, display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${check})`}}>
				{p.mark === 'question' ? (
					<div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 130, color: th.card, lineHeight: 1, transform: `scale(${check})`}}>?</div>
				) : (
					<svg width="110" height="110" viewBox="0 0 100 100"><path d="M20 52 L42 74 L82 30" fill="none" stroke={th.card} strokeWidth="14" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="120" strokeDashoffset={120 * (1 - check)} /></svg>
				)}
			</div>
			<div style={{opacity: k}}>
				<Headline text={p.line ?? ''} th={th} maxSize={96} maxLines={4} highlight={p.highlight} delay={4} />
			</div>
			{p.object ? <Obj name={p.object} th={th} size={240} t={loop(frame, 60)} /> : null}
		</Stage>
	);
};

export const TEMPLATES: Record<string, React.FC<TP>> = {
	hook: Hook,
	question: Question,
	versus: Versus,
	stat: Stat,
	bars: Bars,
	steps: Steps,
	object: ObjectScene,
	study: Study,
	brain: BrainScene,
	gauge: GaugeScene,
	takeaway: Takeaway,
};

export const Scene: React.FC<{template: string; params: Record<string, any>; words: Word[]; dur: number; th: Theme; kicker?: string}> = ({template, params, words, dur, th, kicker}) => {
	const Comp = TEMPLATES[template] ?? ObjectScene;
	const k = useSpring(0, 18);
	const frame = useCurrentFrame();
	const push = interpolate(frame, [0, Math.max(dur, 1)], [1, 1.045], {extrapolateRight: 'clamp'});
	return (
		<AbsoluteFill>
			{kicker ? (
				<div style={{position: 'absolute', top: SAFE.kicker, left: SAFE.left, width: W - SAFE.left - SAFE.right, display: 'flex', opacity: k}}>
					<div style={{display: 'flex', alignItems: 'center', gap: 12, fontFamily: fonts.body, fontWeight: 800, fontSize: 28, letterSpacing: 3, color: th.accent}}>
						<div style={{width: 14, height: 14, borderRadius: 7, background: th.accent}} />
						{kicker.toUpperCase()}
					</div>
				</div>
			) : null}
			<AbsoluteFill style={{transform: `scale(${push})`, transformOrigin: '50% 45%'}}>
				<Comp p={params} th={th} words={words} dur={dur} />
			</AbsoluteFill>
		</AbsoluteFill>
	);
};
