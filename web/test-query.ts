import { getAllSeekers } from './src/lib/queries';
const seekers = getAllSeekers();
const missing = seekers.filter(s => Array.from(["Cao Ngọc Mai", "Lưu Huyền", "Nguyen Viet Hong", "Hoàng Phước"]).includes(s.name));
console.log("Found:", missing.length);
console.dir(missing, { depth: null });
