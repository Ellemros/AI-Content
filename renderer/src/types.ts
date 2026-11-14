export type Word = {w: string; f0: number; f1: number};

export type SceneProps = {
	template: string;
	durationInFrames: number;
	kicker?: string;
	params: Record<string, any>;
	words: Word[]; // relative to the scene's first frame
};

export type Caption = {from: number; to: number; words: Word[]}; // absolute frames

export type ShortProps = {
	theme: string;
	brand: string;
	durationInFrames: number;
	transitionFrames: number;
	scenes: SceneProps[];
	captions: Caption[];
};
