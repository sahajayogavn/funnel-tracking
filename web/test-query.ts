import { getAllSeekers } from './src/lib/queries';
async function main() {
  const seekers = await getAllSeekers();
  const missing = seekers.filter(s => Array.from(["Cao Ngọc Mai", "Lưu Huyền", "Nguyen Viet Hong", "Hoàng Phước"]).includes(s.name));
  console.log("Found:", missing.length);
  console.dir(missing, { depth: null });
}
void main();
