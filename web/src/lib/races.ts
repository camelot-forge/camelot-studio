export interface Race {
	male: number;
	female: number;
	name: string;
	realm: 'Albion' | 'Midgard' | 'Hibernia';
}

// The 18 playable races. The ids are the client's own fig3 rows
// (`fig3map.csv` / `fig3descriptions.csv`): each race maps to two rows,
// male = 2k-1, female = 2k (Briton = 1/2 … Sylvan = 35/36). The 3 Minotaur
// eRace ids (19..21) are not player races and are not listed here.
export const RACES: Race[] = [
	{ male: 1, female: 2, name: 'Briton', realm: 'Albion' },
	{ male: 3, female: 4, name: 'Saracen', realm: 'Albion' },
	{ male: 5, female: 6, name: 'Frostalf', realm: 'Midgard' },
	{ male: 7, female: 8, name: 'Shar', realm: 'Hibernia' },
	{ male: 9, female: 10, name: 'Avalonian', realm: 'Albion' },
	{ male: 11, female: 12, name: 'Elf', realm: 'Hibernia' },
	{ male: 13, female: 14, name: 'Highlander', realm: 'Albion' },
	{ male: 15, female: 16, name: 'Norseman', realm: 'Midgard' },
	{ male: 17, female: 18, name: 'Lurikeen', realm: 'Hibernia' },
	{ male: 19, female: 20, name: 'Kobold', realm: 'Midgard' },
	{ male: 21, female: 22, name: 'Dwarf', realm: 'Midgard' },
	{ male: 23, female: 24, name: 'Inconnu', realm: 'Albion' },
	{ male: 25, female: 26, name: 'Half Ogre', realm: 'Albion' },
	{ male: 27, female: 28, name: 'Valkyn', realm: 'Midgard' },
	{ male: 29, female: 30, name: 'Firbolg', realm: 'Hibernia' },
	{ male: 31, female: 32, name: 'Troll', realm: 'Midgard' },
	{ male: 33, female: 34, name: 'Celt', realm: 'Hibernia' },
	{ male: 35, female: 36, name: 'Sylvan', realm: 'Hibernia' }
];
