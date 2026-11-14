import {loadFont as loadDisplay} from '@remotion/google-fonts/BricolageGrotesque';
import {loadFont as loadBody} from '@remotion/google-fonts/Inter';

const display = loadDisplay('normal', {weights: ['600', '800'], subsets: ['latin']});
const body = loadBody('normal', {weights: ['500', '700', '800'], subsets: ['latin']});

export const fonts = {
	display: display.fontFamily,
	body: body.fontFamily,
};

export type Theme = {
	paper: string;
	paperDeep: string;
	ink: string;
	inkSoft: string;
	accent: string;
	alert: string;
	gold: string;
	card: string;
	line: string;
};

// Money Minds: warm paper, navy ink, money green, coral for cost, gold for reward.
export const moneyMinds: Theme = {
	paper: '#F4EEE3',
	paperDeep: '#EAE1D2',
	ink: '#14213D',
	inkSoft: '#5B6478',
	accent: '#18A06B',
	alert: '#E4573D',
	gold: '#F2B134',
	card: '#FFFFFF',
	line: '#D9CDB8',
};

export const THEMES: Record<string, Theme> = {'money-minds': moneyMinds};

// Vertical layout zones. Platform UI covers the top ~200 px and the bottom ~330 px and the right edge.
export const W = 1080;
export const H = 1920;
export const SAFE = {left: 90, right: 150, top: 210, kicker: 256, stageTop: 330, stageBottom: 1250, captionTop: 1300, captionBottom: 1480};
export const STAGE_W = W - SAFE.left - SAFE.right;
export const STAGE_H = SAFE.stageBottom - SAFE.stageTop;
