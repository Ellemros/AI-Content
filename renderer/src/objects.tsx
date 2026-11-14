import React from 'react';
import {Theme} from './theme';

// One illustration style: flat fills, 7 px navy outline, rounded joins, one highlight.
// Every object is drawn in a 200 x 200 box. `t` (0..1) drives its own small action.

type ObjProps = {t: number; th: Theme; size: number; style?: React.CSSProperties};
const S = (th: Theme) => ({stroke: th.ink, strokeWidth: 7, strokeLinejoin: 'round' as const, strokeLinecap: 'round' as const});

const Box: React.FC<{size: number; style?: React.CSSProperties; children: React.ReactNode}> = ({size, style, children}) => (
	<svg width={size} height={size} viewBox="0 0 200 200" style={{overflow: 'visible', ...style}}>
		{children}
	</svg>
);

export const CreditCard: React.FC<ObjProps & {tilt?: number}> = ({t, th, size, style, tilt = -8}) => (
	<Box size={size} style={style}>
		<g transform={`rotate(${tilt} 100 100)`}>
			<rect x="15" y="50" width="170" height="108" rx="16" fill={th.ink} {...S(th)} />
			<rect x="15" y="72" width="170" height="20" fill="#0B1428" />
			<rect x="34" y="108" width="34" height="26" rx="5" fill={th.gold} stroke="none" />
			<rect x="110" y="128" width="56" height="8" rx="4" fill="#5B6B8C" />
			<rect x="110" y="112" width="36" height="8" rx="4" fill="#5B6B8C" />
			<path d={`M 150 60 q ${8 + 6 * t} 12 0 24`} fill="none" stroke={th.accent} strokeWidth="5" strokeLinecap="round" opacity={t} />
		</g>
	</Box>
);

export const Cash: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		{[2, 1, 0].map((i) => (
			<g key={i} transform={`translate(${i * 8 - 8 * t * i}, ${-i * 10})`}>
				<rect x="12" y="62" width="176" height="92" rx="10" fill={i === 0 ? th.accent : '#13885A'} {...S(th)} />
				<circle cx="100" cy="108" r="24" fill="#BEEBD6" stroke={th.ink} strokeWidth="5" />
				<text x="100" y="119" textAnchor="middle" fontSize="32" fontWeight="800" fill={th.ink} fontFamily="Inter">$</text>
			</g>
		))}
	</Box>
);

export const Coin: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<ellipse cx="100" cy="100" rx={70 * Math.max(0.15, Math.abs(Math.cos(t * Math.PI * 2)))} ry="70" fill={th.gold} {...S(th)} />
		<text x="100" y="118" textAnchor="middle" fontSize="54" fontWeight="800" fill={th.ink} fontFamily="Inter" opacity={Math.abs(Math.cos(t * Math.PI * 2))}>$</text>
	</Box>
);

export const Wallet: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<rect x="40" y={40 - 30 * t} width="110" height="70" rx="8" fill={th.accent} {...S(th)} />
		<rect x="18" y="62" width="164" height="112" rx="18" fill="#7A4E2D" {...S(th)} />
		<rect x="120" y="96" width="62" height="44" rx="12" fill="#9B6A40" {...S(th)} />
		<circle cx="140" cy="118" r="7" fill={th.gold} />
	</Box>
);

export const Receipt: React.FC<ObjProps> = ({t, th, size, style}) => {
	const len = 60 + 110 * t;
	return (
		<Box size={size} style={style}>
			<rect x="40" y="10" width="120" height="26" rx="8" fill={th.inkSoft} {...S(th)} />
			<path d={`M 52 30 L 148 30 L 148 ${30 + len} l -12 -10 l -12 10 l -12 -10 l -12 10 l -12 -10 l -12 10 l -12 -10 l -12 10 l -12 -10 Z`} fill={th.card} {...S(th)} />
			{[0, 1, 2, 3].map((i) => (54 + i * 26 < 30 + len - 20 ? <rect key={i} x="64" y={54 + i * 26} width={i === 3 ? 40 : 72} height="8" rx="4" fill={i === 3 ? th.alert : th.line} /> : null))}
		</Box>
	);
};

export const Brain: React.FC<ObjProps & {glow?: string}> = ({t, th, size, style, glow}) => (
	<Box size={size} style={style}>
		{glow ? <circle cx="100" cy="100" r={70 + 22 * t} fill={glow} opacity={0.25 * t} /> : null}
		<path d="M100 40 C 70 26 36 40 38 72 C 18 84 22 120 44 128 C 46 156 76 166 100 150 C 124 166 154 156 156 128 C 178 120 182 84 162 72 C 164 40 130 26 100 40 Z" fill="#F6B7C1" {...S(th)} />
		<path d="M100 42 L100 150 M66 72 q 14 10 0 24 M134 72 q -14 10 0 24 M60 116 q 18 -4 26 8 M140 116 q -18 -4 -26 8" fill="none" stroke={th.ink} strokeWidth="5" strokeLinecap="round" />
		{glow ? <circle cx="100" cy="112" r={12 + 6 * t} fill={glow} stroke={th.ink} strokeWidth="5" /> : null}
	</Box>
);

export const Cart: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		{[0, 1, 2].map((i) => (
			<rect key={i} x={64 + i * 30} y={60 - 40 * Math.max(0, 1 - t * 2 + i * 0.25)} width="24" height="34" rx="4" fill={[th.accent, th.gold, th.alert][i]} {...S(th)} strokeWidth={5} opacity={t > i * 0.2 ? 1 : 0} />
		))}
		<path d="M20 40 L46 40 L64 130 L160 130 L176 66 L54 66" fill="none" {...S(th)} />
		<circle cx="76" cy="160" r="13" fill={th.ink} />
		<circle cx="150" cy="160" r="13" fill={th.ink} />
	</Box>
);

export const Phone: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<rect x="55" y="12" width="90" height="176" rx="18" fill={th.ink} {...S(th)} />
		<rect x="64" y="30" width="72" height="136" rx="8" fill={th.card} />
		<circle cx="100" cy="98" r={18 + 10 * t} fill={th.accent} opacity={0.9} />
		<path d="M90 98 l8 8 l14 -16" fill="none" stroke={th.card} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity={t > 0.5 ? 1 : 0} />
	</Box>
);

export const Terminal: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<rect x="50" y="40" width="100" height="150" rx="16" fill="#2B3A5C" {...S(th)} />
		<rect x="64" y="56" width="72" height="44" rx="6" fill={t > 0.6 ? th.accent : '#9FB1D6'} />
		{[0, 1, 2].map((r) => [0, 1, 2].map((c) => <rect key={`${r}${c}`} x={66 + c * 24} y={112 + r * 22} width="18" height="14" rx="3" fill="#5B6B8C" />))}
		{[0, 1, 2].map((i) => (
			<path key={i} d={`M ${160 + i * 12} ${60 - i * 4} q 14 20 0 40`} fill="none" stroke={th.accent} strokeWidth="5" strokeLinecap="round" opacity={Math.max(0, Math.sin((t * 3 - i * 0.3) * Math.PI))} />
		))}
	</Box>
);

export const PriceTag: React.FC<ObjProps & {label?: string}> = ({t, th, size, style, label = '$'}) => (
	<Box size={size} style={style}>
		<g transform={`rotate(${-12 + 8 * Math.sin(t * Math.PI * 2)} 60 60)`}>
			<path d="M60 30 L170 30 L170 120 L60 120 L20 75 Z" fill={th.gold} {...S(th)} />
			<circle cx="48" cy="75" r="9" fill={th.paper} stroke={th.ink} strokeWidth="5" />
			<text x="118" y="92" textAnchor="middle" fontSize="44" fontWeight="800" fill={th.ink} fontFamily="Inter">{label}</text>
		</g>
	</Box>
);

export const Gauge: React.FC<ObjProps & {value: number}> = ({th, size, style, value}) => {
	const angle = -180 + 180 * value;
	return (
		<Box size={size} style={style}>
			<path d="M20 140 A 80 80 0 0 1 180 140" fill="none" stroke={th.line} strokeWidth="26" />
			<path d="M20 140 A 80 80 0 0 1 60 70" fill="none" stroke={th.accent} strokeWidth="26" />
			<path d="M140 70 A 80 80 0 0 1 180 140" fill="none" stroke={th.alert} strokeWidth="26" />
			<g transform={`rotate(${angle + 90} 100 140)`}>
				<path d="M100 140 L100 72" stroke={th.ink} strokeWidth="9" strokeLinecap="round" />
			</g>
			<circle cx="100" cy="140" r="14" fill={th.ink} />
		</Box>
	);
};

export const Piggy: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<circle cx="100" cy={20 + 60 * t} r="14" fill={th.gold} {...S(th)} strokeWidth={5} opacity={t < 0.95 ? 1 : 0} />
		<ellipse cx="100" cy="122" rx="70" ry="52" fill="#F6B7C1" {...S(th)} />
		<rect x="88" y="72" width="26" height="8" rx="4" fill={th.ink} />
		<circle cx="168" cy="118" r="14" fill="#F39BAA" {...S(th)} strokeWidth={5} />
		<circle cx="130" cy="106" r="5" fill={th.ink} />
		<path d="M64 168 l0 16 M136 168 l0 16" stroke={th.ink} strokeWidth="10" strokeLinecap="round" />
	</Box>
);

export const Clock: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<circle cx="100" cy="100" r="80" fill={th.card} {...S(th)} />
		<g transform={`rotate(${t * 360} 100 100)`}><path d="M100 100 L100 40" stroke={th.alert} strokeWidth="7" strokeLinecap="round" /></g>
		<g transform={`rotate(${t * 30 + 90} 100 100)`}><path d="M100 100 L100 62" stroke={th.ink} strokeWidth="9" strokeLinecap="round" /></g>
		<circle cx="100" cy="100" r="8" fill={th.ink} />
	</Box>
);

export const Calendar: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<rect x="22" y="36" width="156" height="144" rx="14" fill={th.card} {...S(th)} />
		<rect x="22" y="36" width="156" height="36" rx="14" fill={th.alert} {...S(th)} />
		{Array.from({length: 9}).map((_, i) => (
			<path key={i} d={`M ${48 + (i % 3) * 46} ${100 + Math.floor(i / 3) * 26} l 8 8 l 16 -14`} fill="none" stroke={th.accent} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity={t * 9 > i ? 1 : 0.12} />
		))}
	</Box>
);

export const Person: React.FC<ObjProps & {mood?: 'neutral' | 'happy' | 'worried'; shirt?: string}> = ({t, th, size, style, mood = 'neutral', shirt}) => (
	<Box size={size} style={style}>
		<path d="M40 196 C 40 140 70 122 100 122 C 130 122 160 140 160 196 Z" fill={shirt ?? th.accent} {...S(th)} />
		<circle cx="100" cy="76" r="44" fill="#F2C9A0" {...S(th)} />
		<path d="M58 66 C 60 30 140 28 142 64 C 128 50 92 44 58 66 Z" fill={th.ink} />
		<circle cx="84" cy={80 - (mood === 'happy' ? 2 : 0)} r="5" fill={th.ink} />
		<circle cx="116" cy={80 - (mood === 'happy' ? 2 : 0)} r="5" fill={th.ink} />
		{mood === 'happy' ? <path d="M84 98 q 16 14 32 0" fill="none" stroke={th.ink} strokeWidth="5" strokeLinecap="round" /> : null}
		{mood === 'worried' ? <path d="M86 104 q 14 -10 28 0" fill="none" stroke={th.ink} strokeWidth="5" strokeLinecap="round" /> : null}
		{mood === 'neutral' ? <path d="M88 100 l 24 0" stroke={th.ink} strokeWidth="5" strokeLinecap="round" /> : null}
		{mood === 'worried' ? <path d="M146 60 q 6 10 0 16" fill="none" stroke="#7FC4F0" strokeWidth="6" strokeLinecap="round" opacity={t} /> : null}
	</Box>
);

export const Chart: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<path d="M24 176 L176 176 M24 176 L24 24" stroke={th.ink} strokeWidth="7" strokeLinecap="round" />
		<path d={`M30 150 L70 ${150 - 30 * t} L110 ${150 - 20 * t} L150 ${150 - 100 * t}`} fill="none" stroke={th.accent} strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
	</Box>
);

export const Lightbulb: React.FC<ObjProps> = ({t, th, size, style}) => (
	<Box size={size} style={style}>
		<circle cx="100" cy="84" r={64 + 10 * t} fill={th.gold} opacity={0.25 * t} />
		<path d="M100 26 C 62 26 44 56 50 84 C 54 104 72 114 74 136 L126 136 C 128 114 146 104 150 84 C 156 56 138 26 100 26 Z" fill={t > 0.3 ? th.gold : th.card} {...S(th)} />
		<rect x="76" y="140" width="48" height="26" rx="6" fill={th.inkSoft} {...S(th)} />
	</Box>
);

export const OBJECTS: Record<string, React.FC<any>> = {
	card: CreditCard,
	cash: Cash,
	coin: Coin,
	wallet: Wallet,
	receipt: Receipt,
	brain: Brain,
	cart: Cart,
	phone: Phone,
	terminal: Terminal,
	tag: PriceTag,
	gauge: Gauge,
	piggy: Piggy,
	clock: Clock,
	calendar: Calendar,
	person: Person,
	chart: Chart,
	lightbulb: Lightbulb,
};
export const OBJECT_NAMES = Object.keys(OBJECTS);
