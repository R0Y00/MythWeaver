/** Supports public HTTP deployments and keeps the user's focus after legacy copying. */
export async function copyText(text:string){
  if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(text);return;}catch{/* Try the browser's legacy path. */}}
  const focused=document.activeElement as HTMLElement|null,selection=document.getSelection(),ranges:Range[]=[];
  if(selection)for(let i=0;i<selection.rangeCount;i++)ranges.push(selection.getRangeAt(i).cloneRange());
  const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');area.style.cssText='position:fixed;left:-9999px;top:0;opacity:0';document.body.append(area);
  try{area.focus();area.select();if(!document.execCommand('copy'))throw new Error('COPY_UNAVAILABLE');}
  finally{area.remove();focused?.focus({preventScroll:true});if(selection){selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}}
}
