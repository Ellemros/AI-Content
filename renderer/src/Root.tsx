import React from 'react';
import {Composition} from 'remotion';
import {Short} from './Short';
import {H, W} from './theme';
import {ShortProps} from './types';

const preview: ShortProps = {
	theme: 'money-minds',
	brand: 'Money Minds',
	durationInFrames: 150,
	transitionFrames: 12,
	scenes: [
		{template: 'hook', durationInFrames: 75, params: {headline: 'Why does a card make spending feel easy?', highlight: 'card', object: 'card', object2: 'cash'}, words: []},
		{template: 'stat', durationInFrames: 75, params: {value: 2, suffix: 'x', label: 'Preview only', object: 'tag'}, words: []},
	],
	captions: [],
};

export const Root: React.FC = () => (
	<Composition
		id="Short"
		component={Short as unknown as React.FC<Record<string, unknown>>}
		width={W}
		height={H}
		fps={30}
		durationInFrames={150}
		defaultProps={preview as unknown as Record<string, unknown>}
		calculateMetadata={({props}) => ({durationInFrames: Number((props as unknown as ShortProps).durationInFrames) || 150})}
	/>
);
