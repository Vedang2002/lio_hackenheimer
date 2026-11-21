// Deterministic issue identifiers only; not authentication, signatures or checksums.
export function stableId(value){
  const input=JSON.stringify(value);
  return [0x811c9dc5,0x9e3779b9,0x85ebca6b,0xc2b2ae35].map(seed=>{
    let h=seed;for(let i=0;i<input.length;i++){h^=input.charCodeAt(i);h=Math.imul(h,0x01000193);}return (h>>>0).toString(16).padStart(8,'0');
  }).join('').slice(0,20);
}
