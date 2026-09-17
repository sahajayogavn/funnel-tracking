// code:web-lib-008:program-catalog
// Active classes synchronized from the fixed Google Sheet.  The compact code is
// intentionally human-readable: it is used in the seeker table and as a filter.

export interface Program {
  code: string;
  city: string;
  day: string;
  time: string;
  location: string;
  delivery: 'Online' | 'Offline';
}

export const PROGRAM_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1g2sOGrla4GXmOpn7dHpRjeSnEA28xDBHeNK_RBOZmLM/edit?usp=sharing';

export const PROGRAMS: Program[] = [
  { code: '20h30-Online-HCM', city: 'TP. Hồ Chí Minh', day: 'T2-T6', time: '20:30–21:30', location: 'Zoom', delivery: 'Online' },
  { code: '16h-CN-Đào Duy Anh-HCM', city: 'TP. Hồ Chí Minh', day: 'CN', time: '16:00–17:30', location: '158 Đào Duy Anh', delivery: 'Offline' },
  { code: '14h-CN-Đào Duy Anh-HCM', city: 'TP. Hồ Chí Minh', day: 'CN', time: '14:00–16:00', location: '158 Đào Duy Anh', delivery: 'Offline' },
  { code: '20h-T3-Hoàng Quốc Việt-HN', city: 'Hà Nội', day: 'T3', time: '20:00–21:00', location: '72 ngõ 106 Hoàng Quốc Việt', delivery: 'Offline' },
  { code: '14h30-CN-Vương Thừa Vũ-HN', city: 'Hà Nội', day: 'CN', time: '14:30–15:30', location: '40 Vương Thừa Vũ', delivery: 'Offline' },
  { code: '15h30-CN-Vương Thừa Vũ-HN', city: 'Hà Nội', day: 'CN', time: '15:30–17:00', location: '40 Vương Thừa Vũ', delivery: 'Offline' },
  { code: '21h-T3-T5-T7-Online-HN', city: 'Hà Nội', day: 'T3-T5-T7', time: '21:00–21:30', location: 'Zoom', delivery: 'Online' },
  { code: '8h30-CN-Xô Viết Nghệ Tĩnh-ĐN', city: 'Đà Nẵng', day: 'CN', time: '08:30–10:00', location: '2 Xô Viết Nghệ Tĩnh', delivery: 'Offline' },
  { code: '8h30-CN-Trần Nhân Tông-HA', city: 'Hội An', day: 'CN', time: '08:30–10:00', location: '121 Trần Nhân Tông', delivery: 'Offline' },
];

export function normalizeProgramCity(city?: string | null): string | null | undefined {
  if (!city) return city;
  return ['Hồ Chí Minh', 'Tp. Hồ Chí Minh', 'TP Hồ Chí Minh', 'HCM'].includes(city) ? 'TP. Hồ Chí Minh' : city;
}

export function programsForCity(city?: string | null): Program[] {
  const normalized = normalizeProgramCity(city);
  return PROGRAMS.filter(program => program.city === normalized);
}
