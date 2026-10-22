/* QR scanning: native detector when supported; pinned jsQR decoder for iOS Safari. Frames are decoded on-device. */
'use strict';
let checkinStream = null, checkinTimer = null, checkinScanBusy = false;
let checkinJsqrPromise=null;
function checkinJsQR(){
 if(window.jsQR)return Promise.resolve(window.jsQR);
 if(checkinJsqrPromise)return checkinJsqrPromise;
 checkinJsqrPromise=new Promise((resolve,reject)=>{
  const script=document.createElement('script');
  script.src='https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js';
  script.crossOrigin='anonymous';
  script.onload=()=>window.jsQR?resolve(window.jsQR):reject(Error('QR decoder did not load'));
  script.onerror=()=>reject(Error('QR decoder unavailable — use your iPhone Camera app or paste the pass code'));
  document.head.appendChild(script);
 }).catch(e=>{checkinJsqrPromise=null;throw e});
 return checkinJsqrPromise;
}


function stopCheckinScanner() {
  if (checkinTimer) { clearInterval(checkinTimer); checkinTimer = null; }
  if (checkinStream) {
    checkinStream.getTracks().forEach(track => track.stop());
    checkinStream = null;
  }
  const video = document.getElementById('checkin-camera');
  if (video) video.srcObject = null;
}
window.addEventListener('hashchange', stopCheckinScanner);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopCheckinScanner(); });

function renderCheckin() {
  stopCheckinScanner();
  const data = state.rows || {}, people = data.guests || [],
        stats = data.summary || {}, canEdit = !!data.can_edit;
  const term = state.search.toLowerCase(), filter = state.filter;
  const selected = people.filter(g => {
    const searchable = [g.name,g.email,g.group_name].join(' ').toLowerCase();
    return searchable.includes(term) &&
      (filter === 'All' || (filter === 'Arrived' && g.checked_in_seats > 0)
       || (filter === 'Not arrived' && !g.checked_in_seats)
       || (filter === 'Partial' && g.checked_in_seats > 0 && g.checked_in_seats < g.seat_limit));
  });
  const summary = '<div class="grid kpis">' +
    stat('Checked-in seats',stats.checked_in_seats||0,'Arrived at the event','check') +
    stat('Confirmed seats',stats.confirmed_seats||0,'Confirmed on RSVPs','users') +
    stat('Arrived groups',stats.arrived_groups||0,'Out of '+(stats.invitation_groups||0)+' invitations','calendar') +
    stat('Total invited',stats.invited_seats||0,'Seats on guest list','users') + '</div>';
  const list = selected.map(g => {
    const count = Number(g.checked_in_seats)||0,
          full = g.seat_limit > 0 && count === g.seat_limit,
          status = count === 0 ? 'Not arrived' : full ? 'Fully arrived' : 'Partial arrival';
    const action = (canEdit && (g.seat_limit > 0 || count > 0)) ? '<div class="checkin-controls">' +
      '<label>Seats arrived <input class="checkin-number" id="checkin-seats-'+g.id+'" type="number" min="0" max="'+g.seat_limit+'" step="1" value="'+count+'" aria-label="Checked in seats for '+esc(g.name)+'"></label>' +
      '<button class="btn primary" onclick="saveCheckinSeats(\''+g.id+'\')">Save</button>' +
      (full ? '' : '<button class="btn" onclick="setGuestCheckin(\''+g.id+'\','+g.seat_limit+')">Check in all</button>') +
      (count > 0 ? '<button class="btn" onclick="setGuestCheckin(\''+g.id+'\',0)">Undo</button>' : '') +
      '</div>' : '';
    return '<article class="card checkin-guest"><div class="checkin-person">'+
      '<div><strong>'+esc(g.name)+'</strong><div class="tiny-note">'+esc(g.group_name||'No group')+' · '+esc(g.email||'No email')+'</div>'+
      '<div class="tiny-note">RSVP: '+esc(g.rsvp)+' · '+g.seat_limit+' confirmed seats</div></div>'+
      '<div class="checkin-right"><span class="badge '+(full?'good':count?'blue':'warn')+'">'+esc(status)+'</span>'+
      '<strong>'+count+' / '+g.seat_limit+'</strong></div></div>'+
      '<div class="checkin-row-actions">'+action+
      '<button class="btn" onclick="printCheckinPass(\''+g.id+'\')">Print QR pass</button></div></article>';
  }).join('');
  const scanner = canEdit ? '<div class="card pad checkin-scanner">'+
    '<div class="section-title"><h2>Scan a guest pass</h2></div>'+
    '<p class="small-note">iPhone Safari: allow rear-camera access over HTTPS. If the scanner cannot open, use the iPhone Camera app to scan the guest QR pass, open its link and confirm after staff sign-in. Only owners and editors can register arrivals.</p>'+
    '<div class="checkin-scan-tools"><button class="btn" id="scan-start" onclick="startCheckinScanner()">Start camera</button>'+
    '<button class="btn" onclick="stopCheckinScanner()">Stop camera</button></div>'+
    '<video id="checkin-camera" class="checkin-video" autoplay muted playsinline></video>'+
    '<canvas id="checkin-canvas" hidden></canvas>'+
    '<label class="field"><span>Scan QR from a saved photo (optional)</span><input type="file" accept="image/*" onchange="checkinPhotoQR(event)"></label>'+
    '<div class="checkin-scan-tools"><input id="checkin-code" class="search" placeholder="Paste complete QR pass code" aria-label="QR pass code">'+
    '<button class="btn primary" onclick="submitCheckinCode()">Check in pass</button></div>'+
    '<p id="checkin-scan-message" class="small-note" role="status"></p></div>' : '';
  const actions = '<button class="btn" onclick="checkinDownload()">Export attendance CSV</button>'+
    '<button class="btn" onclick="checkinHistory()">View history</button>'+
    '<button class="btn" onclick="printAllCheckinPasses()">Print all passes</button>';
  return head('Event-day guest check-in','Track arrivals and partial party check-ins for this event.',actions) +
    summary + scanner +
    '<div class="toolbar m-top"><input class="search" placeholder="Find a guest…" aria-label="Find a guest" value="'+esc(state.search)+'" oninput="searchRows(this.value)">'+
    '<select class="select" aria-label="Attendance filter" onchange="state.filter=this.value;render()">'+
    ['All','Arrived','Not arrived','Partial'].map(v=>'<option '+(v===filter?'selected':'')+'>'+v+'</option>').join('')+'</select></div>'+
    '<div class="checkin-list">'+(selected.length?list:empty('No matching guests','Try a different search or add guests in Guests & RSVP.'))+'</div>'+
    '<p class="small-note">Check-in counts are separate from RSVP responses. Each change is recorded in the audit history. A completed event remains viewable; a cancelled event cannot accept check-ins.</p>';
}
async function setGuestCheckin(gid, seats) {
  try {
    const r = await api(evpath()+'/checkin/'+gid,{method:'POST',body:{seats}});
    toast(r.checked_in_seats ? 'Attendance updated for '+r.name : 'Arrival reset for '+r.name);
    await refresh();
  } catch (e) { toast(e.message); }
}
function saveCheckinSeats(gid) {
  const item = (state.rows.guests||[]).find(g=>g.id===gid);
  const input = document.getElementById('checkin-seats-'+gid);
  if (!item || !input) return;
  const num = Number(input.value);
  if (!input.value.trim() || !Number.isInteger(num) || num < 0 || num > item.seat_limit) {
    toast('Enter a whole number between 0 and '+item.seat_limit); return;
  }
  setGuestCheckin(gid,num);
}
function checkinDownload() { location.href = '/api'+evpath()+'/checkin/export.csv'; }
async function checkinHistory() {
  try {
    const data=await api(evpath()+'/checkin/history');
    const rows=data.length?data.map(h=>'<div class="attention-row"><div><strong>'+esc(h.guest_name)+'</strong><div class="tiny-note">'+
      esc(h.changed_at)+' · '+esc(h.operator_name)+'</div></div><span>'+h.previous_seats+' → '+h.new_seats+'</span></div>').join('')
      :'<p>No attendance changes recorded.</p>';
    document.getElementById('modalRoot').innerHTML =
      '<div class="modal-overlay" onclick="if(event.target===this)closeModal()"><section class="modal" role="dialog" aria-modal="true">'+
      '<header class="modal-head"><h2>Latest check-in activity</h2><button class="xclose" onclick="closeModal()">×</button></header>'+
      '<div class="modal-body">'+rows+'</div></section></div>';
  } catch(e) { toast(e.message); }
}
function checkinBadgeHtml(g) {
  const src = '/api'+evpath()+'/checkin/'+encodeURIComponent(g.id)+'/qr.svg';
  return '<section class="pass"><h1>'+esc(state.event.name)+'</h1><h2>'+esc(g.name)+'</h2>'+
    '<p>'+esc(g.group_name||'Guest')+' · '+g.seat_limit+' seat'+(g.seat_limit===1?'':'s')+'</p>'+
    '<img src="'+src+'" width="210" height="210" alt="Guest QR pass"><small>Present at the event entrance</small></section>';
}
function printCheckinPass(gid) {
  const g=(state.rows.guests||[]).find(person=>person.id===gid);
  if (g) openCheckinPrint([g]);
}
function printAllCheckinPasses() {
  const guests=(state.rows.guests||[]).filter(g=>g.seat_limit>0);
  if(!guests.length){toast('Add guests before printing passes');return}
  if(guests.length>200 && !confirm('Print '+guests.length+' passes?'))return;
  openCheckinPrint(guests);
}
function openCheckinPrint(guests) {
  const popup=window.open('','_blank');
  if(!popup){toast('Allow pop-ups to print guest passes');return}
  const style='<style>body{font:16px system-ui;margin:0;color:#183349}.pass{width:340px;min-height:440px;border:1px solid #ccd9df;border-radius:12px;padding:20px;margin:15px;display:inline-flex;vertical-align:top;flex-direction:column;align-items:center;justify-content:center;text-align:center;break-inside:avoid;page-break-inside:avoid}.pass h1{font-size:15px;margin:6px}.pass h2{font-size:23px;margin:12px}.pass p{font-size:13px}.pass small{font-size:11px}.pass img{margin:12px}@media print{.pass{border:1px solid #ddd;margin:5px}@page{margin:8mm}}</style>';
  popup.document.open();
  popup.document.write('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Honestly Zen Event Planning guest passes</title>'+style+'</head><body>'+guests.map(checkinBadgeHtml).join('')+'</body></html>');
  popup.document.close();
  // Print after all authenticated QR images have resolved.
  const images=Array.from(popup.document.images);
  Promise.all(images.map(img=>img.complete?Promise.resolve():new Promise(resolve=>{
    img.addEventListener('load',resolve,{once:true});
    img.addEventListener('error',resolve,{once:true});
  }))).then(()=>{if(!popup.closed){popup.focus();popup.print();}});
}
async function submitCheckinCode() {
  const input=document.getElementById('checkin-code'), msg=document.getElementById('checkin-scan-message');
  if(!input || !input.value.trim() || checkinScanBusy)return;
  checkinScanBusy=true;
  try {
    const code=input.value.trim();
    const r=await api(evpath()+'/checkin/scan',{method:'POST',body:{code}});
    stopCheckinScanner();
    toast(r.name+' checked in: '+r.checked_in_seats+' seat(s)');
    await refresh();
  } catch(e) { if(msg)msg.textContent=e.message;toast(e.message); }
  finally { checkinScanBusy=false; }
}
async function processScannedCode(code){
 const info=document.getElementById('checkin-scan-message');
 if(!String(code||'').includes('/checkin/')){
  if(info)info.textContent='This is not an Honestly Zen Event Planning guest pass.';
  return;
 }
 stopCheckinScanner();
 const r=await api(evpath()+'/checkin/scan',{method:'POST',body:{code}});
 toast(r.name+' checked in: '+r.checked_in_seats+' seat(s)');
 await refresh();
}
async function checkinPhotoQR(event){
 const file=event.target.files?.[0];if(!file)return;
 const message=document.getElementById('checkin-scan-message');
 try{
  if(file.size>12*1024*1024)throw Error('Image is too large');
  const decode=await checkinJsQR();
  const bitmap=await createImageBitmap(file);
  const canvas=document.getElementById('checkin-canvas'),ctx=canvas.getContext('2d',{willReadFrequently:true});
  const scale=Math.min(1,1200/Math.max(bitmap.width,bitmap.height));
  canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
  ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
  const image=ctx.getImageData(0,0,canvas.width,canvas.height);
  const code=decode(image.data,canvas.width,canvas.height,{inversionAttempts:'attemptBoth'});
  if(!code?.data)throw Error('No QR pass found in that photo');
  await processScannedCode(code.data);
 }catch(e){if(message)message.textContent=e.message;toast(e.message)}
 finally{event.target.value=''}
}
async function startCheckinScanner() {
 const info=document.getElementById('checkin-scan-message'),video=document.getElementById('checkin-camera');
 if(!video||!info)return;
 if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){
  info.textContent='Camera access requires HTTPS on a supported browser. Use the iPhone Camera app to open the QR link, or paste a pass code.';
  return;
 }
 try{
  stopCheckinScanner();
  let detector=null,jsqr=null;
  if('BarcodeDetector' in window){
   try{detector=new BarcodeDetector({formats:['qr_code']})}catch(e){detector=null}
  }
  if(!detector)jsqr=await checkinJsQR();
  checkinStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
  video.srcObject=checkinStream;
  await video.play();video.style.display='block';
  info.textContent='Point the rear camera at the QR pass. Scanning happens on your phone.';
  const canvas=document.getElementById('checkin-canvas');
  checkinTimer=setInterval(async()=>{
   if(checkinScanBusy||video.readyState<2||!checkinStream)return;
   checkinScanBusy=true;
   try{
    let code='';
    if(detector){const result=await detector.detect(video);code=result[0]?.rawValue||''}
    else{
     const scale=Math.min(1,720/video.videoWidth),width=Math.max(1,Math.round(video.videoWidth*scale)),height=Math.max(1,Math.round(video.videoHeight*scale));
     canvas.width=width;canvas.height=height;
     const ctx=canvas.getContext('2d',{willReadFrequently:true});
     ctx.drawImage(video,0,0,width,height);
     const rgba=ctx.getImageData(0,0,width,height);
     code=jsqr(rgba.data,width,height,{inversionAttempts:'attemptBoth'})?.data||'';
    }
    if(code)await processScannedCode(code);
   }catch(e){if(info)info.textContent=e.message}
   finally{checkinScanBusy=false}
  },350);
 }catch(e){stopCheckinScanner();info.textContent='Camera unavailable: '+e.message+'. You can use the iPhone Camera app, QR photo or manual check-in.'}
}
