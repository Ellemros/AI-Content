import {linearTiming, springTiming, TransitionSeries} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {slide} from '@remotion/transitions/slide';
import {wipe} from '@remotion/transitions/wipe';
import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {fitLines, Scene} from './scenes';
import {fonts, H, SAFE, Theme, THEMES, W} from './theme';
import {Caption, ShortProps} from './types';

const Paper: React.FC<{th: Theme}> = ({th}) => {
	const frame = useCurrentFrame();
	const drift = Math.sin(frame / 90) * 40;
	return (
		<AbsoluteFill style={{background: th.paper}}>
			<svg width={W} height={H} style={{position: 'absolute', inset: 0}}>
				<defs>
					<pattern id="dots" width="44" height="44" patternUnits="userSpaceOnUse">
						<circle cx="2" cy="2" r="2" fill={th.line} opacity="0.55" />
					</pattern>
				</defs>
				<rect width={W} height={H} fill="url(#dots)" />
				<circle cx={880 + drift} cy={520} r={330} fill={th.paperDeep} opacity="0.7" />
				<circle cx={160 - drift} cy={1260} r={260} fill={th.paperDeep} opacity="0.55" />
			</svg>
		</AbsoluteFill>
	);
};

const Captions: React.FC<{captions: Caption[]; th: Theme}> = ({captions, th}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const current = captions.find((c) => frame >= c.from && frame < c.to);
	if (!current) return null;
	const text = current.words.map((w) => w.w).join(' ');
	const {size} = fitLines(text, W - SAFE.left - SAFE.right - 80, 64, 2, fonts.body, '800');
	const k = spring({frame: frame - current.from, fps, config: {damping: 16, mass: 0.5}, durationInFrames: 8});
	return (
		<div style={{position: 'absolute', top: SAFE.captionTop, height: SAFE.captionBottom - SAFE.captionTop, left: SAFE.left, width: W - SAFE.left - SAFE.right, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
			<div style={{background: th.card, borderRadius: 28, padding: '18px 34px', border: `5px solid ${th.ink}`, boxShadow: '0 10px 0 rgba(20,33,61,0.14)', maxWidth: W - SAFE.left - SAFE.right, textAlign: 'center', fontFamily: fonts.body, fontWeight: 800, fontSize: size, lineHeight: 1.18, color: th.ink, transform: `scale(${0.92 + 0.08 * k})`, opacity: k}}>
				{current.words.map((w, i) => {
					const active = frame >= w.f0 && frame < w.f1 + 2;
					return (
						<span key={i} style={{color: active ? th.accent : th.ink}}>
							{w.w}
							{i < current.words.length - 1 ? ' ' : ''}
						</span>
					);
				})}
			</div>
		</div>
	);
};

const Progress: React.FC<{th: Theme; total: number; brand: string}> = ({th, total, brand}) => {
	const frame = useCurrentFrame();
	const width = interpolate(frame, [0, total], [0, W - 2 * SAFE.left], {extrapolateRight: 'clamp'});
	return (
		<>
			<div style={{position: 'absolute', top: SAFE.top - 30, left: SAFE.left, width: W - 2 * SAFE.left, height: 10, borderRadius: 5, background: th.paperDeep}} />
			<div style={{position: 'absolute', top: SAFE.top - 30, left: SAFE.left, width, height: 10, borderRadius: 5, background: th.accent}} />
			<div style={{position: 'absolute', top: SAFE.top + 4, left: SAFE.left, fontFamily: fonts.display, fontWeight: 800, fontSize: 28, letterSpacing: 3, color: th.ink, opacity: 0.85}}>{brand.toUpperCase()}</div>
		</>
	);
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const presentation = (index: number): any => {
	const kind = index % 3;
	if (kind === 0) return slide({direction: 'from-right'});
	if (kind === 1) return wipe({direction: 'from-bottom-left'});
	return slide({direction: 'from-bottom'});
};

export const Short: React.FC<ShortProps> = ({theme, brand, scenes, captions, transitionFrames, durationInFrames}) => {
	const th = THEMES[theme] ?? THEMES['money-minds'];
	const T = transitionFrames;
	return (
		<AbsoluteFill style={{background: th.paper}}>
			<Paper th={th} />
			<TransitionSeries>
				{scenes.map((scene, i) => {
					const dur = scene.durationInFrames + (i < scenes.length - 1 ? T : 0);
					return (
						<React.Fragment key={i}>
							{i > 0 ? (
								<TransitionSeries.Transition presentation={presentation(i)} timing={i % 3 === 1 ? linearTiming({durationInFrames: T}) : springTiming({durationInFrames: T, config: {damping: 200}})} />
							) : null}
							<TransitionSeries.Sequence durationInFrames={dur}>
								<Scene template={scene.template} params={scene.params} words={scene.words} dur={dur} th={th} kicker={scene.kicker} />
							</TransitionSeries.Sequence>
						</React.Fragment>
					);
				})}
			</TransitionSeries>
			<Progress th={th} total={durationInFrames} brand={brand} />
			<Captions captions={captions} th={th} />
		</AbsoluteFill>
	);
};

// Unused import guard for fade (kept for future scene types).
export const _fade = fade;
