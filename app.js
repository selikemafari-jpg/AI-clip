const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];

const clips = [
  {id:1,title:"Unexpected reaction",duration:"00:34",date:"Today · 10:42 AM",status:"review",reason:"Laughter and a surprising response could make a strong standalone moment.",art:"",caption:"THAT WAS UNEXPECTED!",kind:"Reaction"},
  {id:2,title:"The story nobody expected",duration:"00:48",date:"Today · 10:18 AM",status:"review",reason:"A complete story beat with a strong opening and a natural ending.",art:"art-two",caption:"WAIT UNTIL YOU HEAR THIS...",kind:"Story"},
  {id:3,title:"A genuinely good point",duration:"00:29",date:"Yesterday · 8:31 PM",status:"published",reason:"A concise, useful statement that may work as an educational short.",art:"art-three",caption:"HERE'S THE IMPORTANT PART",kind:"Insight"}
];
let nextId = 4;
let activeFilter = "all";
let selectedClipId = 1;
let demoLive = false;
let detectionEnabled = true;
let connected = false;

const icon = name => `<i data-lucide="${name}"></i>`;
function renderIcons(){ if(window.lucide) lucide.createIcons(); }
function escapeHTML(str=""){return String(str).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function statusLabel(status){return ({review:"Needs review",approved:"Approved",published:"Published"})[status]||status;}
function statusClass(status){return "status-"+status;}
function toast(message, iconName="check-circle"){
  const el=document.createElement("div");el.className="toast";el.innerHTML=`${icon(iconName)}<span>${escapeHTML(message)}</span>`;
  $("#toastRegion").appendChild(el);renderIcons();setTimeout(()=>{el.style.opacity="0";el.style.transform="translateY(6px)";setTimeout(()=>el.remove(),250)},3200);
}
function showModal(title,message){
  $("#modalTitle").textContent=title;$("#modalText").textContent=message;$("#modalBackdrop").classList.remove("hidden");renderIcons();
}
function closeModal(){$("#modalBackdrop").classList.add("hidden");}
function navigate(page){
  const target=$("#page-"+page);if(!target)return;
  $$(".page").forEach(p=>p.classList.toggle("active",p===target));
  $$(".nav-item[data-page]").forEach(b=>b.classList.toggle("active",b.dataset.page===page));
  const names={overview:"Overview",live:"Live Studio",clips:"My Clips",editor:"Clip Editor",publishing:"Publishing",settings:"Settings"};
  $("#breadcrumbCurrent").textContent=names[page]||"Overview";
  $("#sidebar").classList.remove("open");
  window.scrollTo({top:0,behavior:"smooth"});
  if(page==="clips")renderClips();if(page==="publishing")renderQueue();if(page==="editor")loadEditor();
}
document.addEventListener("click",e=>{
  const nav=e.target.closest("[data-page]");if(nav){navigate(nav.dataset.page);return;}
});
$("#mobileMenu").addEventListener("click",()=>$("#sidebar").classList.toggle("open"));
$("#helpButton").addEventListener("click",()=>showModal("ClipStream AI starter","This is a front-end prototype. The Twitch connection, AI detection, video rendering, database and direct social publishing still need to be implemented in a secure backend."));
$("#profileMenu").addEventListener("click",()=>showModal("Demo workspace","You're viewing a local prototype. Account login and user profiles have not been connected yet."));
$("#modalClose").addEventListener("click",closeModal);$("#modalOkay").addEventListener("click",closeModal);
$("#modalBackdrop").addEventListener("click",e=>{if(e.target===$("#modalBackdrop"))closeModal();});
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeModal();});

function renderRecent(){
  const recent=clips.slice().sort((a,b)=>b.id-a.id).slice(0,3);
  $("#recentClipList").innerHTML=recent.map(c=>`<div class="recent-item"><div class="clip-thumb ${c.art}">${icon("play")}</div><div class="recent-copy"><strong>${escapeHTML(c.title)}</strong><small>${escapeHTML(c.duration)} · ${escapeHTML(c.date)}</small></div><span class="clip-status ${statusClass(c.status)}">${statusLabel(c.status)}</span></div>`).join("");
  $("#statClips").textContent=clips.length;$("#statReview").textContent=clips.filter(c=>c.status==="review").length;$("#statPublished").textContent=clips.filter(c=>c.status==="published").length;$("#navClipCount").textContent=clips.length;$("#statChannels").textContent=connected?1:0;
}
function renderClips(){
  const q=$("#clipSearch").value.trim().toLowerCase();
  const filtered=clips.filter(c=>(activeFilter==="all"||c.status===activeFilter)&&(`${c.title} ${c.reason} ${c.kind}`).toLowerCase().includes(q));
  $("#clipGrid").innerHTML=filtered.map(c=>`<article class="clip-card"><div class="clip-art ${c.art}"><span class="clip-kind">${escapeHTML(c.kind)}</span>${icon("play-circle")}<span class="clip-duration">${escapeHTML(c.duration)}</span></div><div class="clip-card-body"><h3 class="clip-card-title" title="${escapeHTML(c.title)}">${escapeHTML(c.title)}</h3><div class="clip-meta"><span>${escapeHTML(c.date)}</span><span class="clip-status ${statusClass(c.status)}">${statusLabel(c.status)}</span></div><p class="clip-reason">${escapeHTML(c.reason)}</p><div class="clip-card-footer"><button class="button button-secondary edit-clip" data-id="${c.id}">${icon("pencil")} Edit details</button><div class="small-actions">${c.status==="review"?`<button class="icon-button approve-clip" data-id="${c.id}" aria-label="Approve clip" title="Approve clip">${icon("check")}</button>`:""}<button class="icon-button delete-clip" data-id="${c.id}" aria-label="Delete demo clip" title="Delete demo clip">${icon("trash-2")}</button></div></div></div></article>`).join("");
  $("#noClips").classList.toggle("hidden",filtered.length>0);
  renderIcons();
}
$("#clipSearch").addEventListener("input",renderClips);
$$(".filter-tab").forEach(btn=>btn.addEventListener("click",()=>{$$(".filter-tab").forEach(b=>b.classList.toggle("active",b===btn));activeFilter=btn.dataset.filter;renderClips();}));
$("#clipGrid").addEventListener("click",e=>{
  const edit=e.target.closest(".edit-clip");const approve=e.target.closest(".approve-clip");const del=e.target.closest(".delete-clip");
  if(edit){selectedClipId=Number(edit.dataset.id);navigate("editor");return;}
  if(approve){const c=clips.find(x=>x.id===Number(approve.dataset.id));if(c){c.status="approved";renderAll();toast("Clip approved and added to the publishing queue.");}return;}
  if(del){const id=Number(del.dataset.id);const c=clips.find(x=>x.id===id);if(c&&confirm(`Delete demo clip "${c.title}" from this browser?`)){clips.splice(clips.findIndex(x=>x.id===id),1);renderAll();toast("Demo clip removed.","trash-2");}}
});
$("#createDemoClip").addEventListener("click",()=>{
  const c={id:nextId++,title:"New live moment",duration:"00:32",date:"Just now · Demo",status:"review",reason:"Sample detected moment. Replace this with real AI analysis in your backend.",art:["","art-two","art-three","art-four"][Math.floor(Math.random()*4)],caption:"A MOMENT WORTH SHARING",kind:"New moment"};
  clips.unshift(c);renderAll();toast("A demo clip was added. No real video was created."); 
});
function loadEditor(){
  let c=clips.find(x=>x.id===selectedClipId);if(!c)c=clips[0];if(!c){$("#editorClipName").textContent="No clip selected";return;}
  selectedClipId=c.id;$("#editorClipName").textContent=c.title;$("#editorTitle").value=c.title;$("#editorCaption").value=c.caption||"";$("#captionSample").textContent=c.caption||"YOUR CAPTION HERE";$("#editorFormat").value="9:16";$("#editorNotes").value="";
}
$("#editorCaption").addEventListener("input",()=>$("#captionSample").textContent=$("#editorCaption").value||"YOUR CAPTION HERE");
$("#editorTitle").addEventListener("input",()=>$("#editorClipName").textContent=$("#editorTitle").value||"Untitled clip");
$("#saveEditor").addEventListener("click",()=>{
  const c=clips.find(x=>x.id===selectedClipId);if(!c){toast("Select a clip first.","info");return;}
  c.title=$("#editorTitle").value.trim()||"Untitled clip";c.caption=$("#editorCaption").value.trim();c.format=$("#editorFormat").value;c.notes=$("#editorNotes").value.trim();
  renderAll();loadEditor();toast("Clip details saved in this browser.");
});
function renderQueue(){
  const approved=clips.filter(c=>c.status==="approved");
  const published=clips.filter(c=>c.status==="published");
  const items=[...approved,...published];
  $("#publishingQueue").innerHTML=items.length?items.map(c=>`<div class="queue-item"><div class="clip-thumb ${c.art}">${icon("film")}</div><div class="queue-copy"><strong>${escapeHTML(c.title)}</strong><small>${c.status==="published"?"Marked as published in demo":"Approved · Ready for your next step"}</small></div><span class="clip-status ${statusClass(c.status)}">${statusLabel(c.status)}</span>${c.status==="approved"?`<button class="button button-primary mark-published" data-id="${c.id}">Mark published</button>`:""}</div>`).join(""):`<div class="empty-state"><div class="empty-icon">${icon("send")}</div><strong>Your queue is clear</strong><p>Approve clips in My Clips to see them here.</p></div>`;
  renderIcons();
}
$("#publishingQueue").addEventListener("click",e=>{
  const btn=e.target.closest(".mark-published");if(!btn)return;const c=clips.find(x=>x.id===Number(btn.dataset.id));if(c){c.status="published";renderAll();toast("Marked as published in the demo. No post was sent to a social platform.");}
});
$("#refreshQueue").addEventListener("click",()=>{renderQueue();toast("Publishing queue refreshed.");});
$$(".connect-platform").forEach(btn=>btn.addEventListener("click",()=>showModal(`${btn.dataset.platform} integration`, `Direct ${btn.dataset.platform} publishing is not implemented in this prototype. Before adding it, review the platform's current developer API, OAuth requirements, publishing permissions and app-review rules.`)));

function setLiveState(live){
  demoLive=live;
  $("#streamPreviewTitle").textContent=live?"Demo stream is live":"Your stream starts here";
  $("#streamPreviewText").textContent=live?"Simulated podcast stream running. Sample moments may appear below.":"Connect your channel to check its live status.";
  $("#previewStatus").textContent=live?"DEMO STREAM ACTIVE":"WAITING FOR CONNECTION";
  $("#connectionBadge").innerHTML=`<span class="status-dot ${live?"green":"gray"}"></span> ${live?"Demo stream active":"Not connected"}`;
  $("#connectTwitch").innerHTML=`${icon("link-2")} Connect Twitch`;
  $("#simulateLive").innerHTML=live?`${icon("square")} Stop demo stream`:`${icon("flask-conical")} Try demo stream`;
  $("#twitchSettingStatus").textContent=connected?"Connected (demo only)":"Not connected · Demo only";
  renderIcons();
}
$("#connectTwitch").addEventListener("click",()=>showModal("Twitch connection not configured","This button is a placeholder. To connect real Twitch accounts, build a backend OAuth flow, register a Twitch developer application, request the required broadcaster permission, validate OAuth state, and keep client secrets on the server. Never put a client secret in frontend JavaScript."));
$("#settingsTwitch").addEventListener("click",()=>showModal("Twitch connection not configured","Real account connection requires a backend OAuth flow. The demo connection below does not access Twitch or create real clips."));
$("#simulateLive").addEventListener("click",()=>{
  if(demoLive){setLiveState(false);$("#activityFeed").innerHTML=`<div class="empty-state"><div class="empty-icon">${icon("audio-lines")}</div><strong>Demo stream stopped</strong><p>Start the demo again to see sample detection events.</p></div>`;renderIcons();toast("Demo stream stopped.","square");return;}
  setLiveState(true);connected=true;$("#statChannels").textContent="1";$("#twitchSettingStatus").textContent="Demo active · Not a real Twitch connection";
  if(!detectionEnabled){$("#activityFeed").innerHTML=`<div class="empty-state"><div class="empty-icon">${icon("pause")}</div><strong>Detection is paused</strong><p>Turn moment detection on to display simulated signals.</p></div>`;renderIcons();return;}
  const events=[
    {title:"Chat activity spike",desc:"Sample signal: chat messages increased around a conversational moment.",time:"Just now",ico:"messages-square"},
    {title:"Potential funny moment",desc:"Sample signal: laughter-like audio activity detected in demo mode.",time:"A few seconds ago",ico:"audio-lines"},
    {title:"Clip candidate ready",desc:"Sample candidate queued for review. No real clip has been created.",time:"Moments ago",ico:"sparkles"}
  ];
  $("#activityFeed").innerHTML=events.map(ev=>`<div class="activity-item"><span class="activity-icon">${icon(ev.ico)}</span><div class="activity-copy"><strong>${ev.title}</strong><p>${ev.desc}</p><small>${ev.time}</small></div></div>`).join("");renderIcons();toast("Demo stream started. These are simulated events.","radio-tower");
});
$("#detectionToggle").addEventListener("change",e=>{detectionEnabled=e.target.checked;$("#settingsDetection").checked=detectionEnabled;if(!detectionEnabled){$("#activityFeed").innerHTML=`<div class="empty-state"><div class="empty-icon">${icon("pause")}</div><strong>Detection paused</strong><p>Turn moment detection on to resume sample signals.</p></div>`;renderIcons();toast("Demo moment detection paused.","pause");}else if(demoLive){$("#simulateLive").click();$("#simulateLive").click();}else toast("Demo moment detection enabled.");});
$("#settingsDetection").addEventListener("change",e=>{$("#detectionToggle").checked=e.target.checked;$("#detectionToggle").dispatchEvent(new Event("change"));});
$("#approvalRequired").addEventListener("change",e=>toast(e.target.checked?"Approval before publishing is enabled.":"Approval requirement disabled in preferences only.","shield-check"));
$("#defaultFormat").addEventListener("change",e=>toast("Default format set to "+e.target.value+"."));
$("#editorFormat").addEventListener("change",e=>toast("Preview format preference updated. Actual video rendering is not connected.","crop"));

const API_BASE_URL = "http://127.0.0.1:8000";

async function syncBackendStatus() {
  const statusElement = $("#backendApiStatus");

  if (!statusElement) return;

  statusElement.textContent = "Checking backend connection...";

  try {
    const response = await fetch(
      `${API_BASE_URL}/api/dashboard`
    );

    if (!response.ok) {
      throw new Error(`HTTP error: ${response.status}`);
    }

    const dashboard = await response.json();

    statusElement.textContent =
      `Backend connected | Stream: ${dashboard.stream_status} | ` +
      `Clips ready: ${dashboard.clips_ready} | ` +
      `Detected: ${dashboard.clips_detected} | ` +
      `Failed: ${dashboard.clips_failed}`;

    console.log("ClipStream API dashboard:", dashboard);

  } catch (error) {
    statusElement.textContent =
      "Backend unavailable. Check that the server is running on port 8000.";

    console.error("ClipStream API request failed:", error);
  }
}

function renderAll(){renderRecent();renderClips();renderQueue();renderIcons();}
renderAll();
setLiveState(false);
renderIcons();
syncBackendStatus();

// Real video clipping with the ClipStream AI backend
const videoClipForm = document.getElementById("videoClipForm");

if (videoClipForm) {
  videoClipForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const videoInput = document.getElementById("videoFile");
    const startInput = document.getElementById("clipStart");
    const endInput = document.getElementById("clipEnd");
    const status = document.getElementById("videoClipStatus");
    const result = document.getElementById("videoClipResult");
    const downloadLink = document.getElementById("downloadVideoClip");
    const submitButton = document.getElementById("createVideoClip");

    const video = videoInput.files[0];
    const start = Number(startInput.value);
    const end = Number(endInput.value);

    if (!video) {
      status.textContent = "Please choose a video file.";
      return;
    }

    if (video.size > 500 * 1024 * 1024) {
      status.textContent = "Your video exceeds the 500 MB limit.";
      return;
    }

    if (
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end <= start ||
      end - start > 300
    ) {
      status.textContent =
        "Choose valid start and end times. Clips must be between 0 and 300 seconds long.";
      return;
    }

    const formData = new FormData();
    formData.append("video", video);
    formData.append("start_seconds", String(start));
    formData.append("end_seconds", String(end));

    submitButton.disabled = true;
    status.textContent = "Uploading video and creating your clip...";
    result.hidden = true;

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/clips/trim`,
        {
          method: "POST",
          body: formData
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || `Request failed with status ${response.status}`
        );
      }

      downloadLink.href = `${API_BASE_URL}${data.download_url}`;
      result.hidden = false;

      status.textContent =
        `Success! Your ${data.duration_seconds}-second clip is ready.`;

      console.log("Clip created successfully:", data);

      if (typeof syncBackendStatus === "function") {
        syncBackendStatus();
      }
    } catch (error) {
      status.textContent = `Could not create clip: ${error.message}`;
      console.error("Video clipping failed:", error);
    } finally {
      submitButton.disabled = false;
    }
  });
}