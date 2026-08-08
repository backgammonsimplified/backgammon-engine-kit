from __future__ import annotations
import argparse, csv, importlib, importlib.metadata, json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from .calculator_reference import BackgammonCalculatorReference
from .engine import EngineKitResearchAdapter
from .evidence import split_exported_position
from .gallery_html import e, method_card, reference_card, render_page
from .gnu_cli import GnuBackgammonCli
from .r_oracle import BglabGnuidOracle
from .renderer import BackgammonBoardRenderer

LABELS={"native_python":"Native Python","engine_kit":"Engine Kit public API","ankigammon_direct":"Direct AnkiGammon"}
@dataclass(frozen=True)
class Case: case_id:str; label:str; xgid:str; gnuid:str

def load_cases(path:Path)->list[Case]:
    with path.open(newline="",encoding="utf-8") as h:return [Case(**r) for r in csv.DictReader(h)]
def _bek():return importlib.import_module("backgammon_engine_kit")
def _canonical(i:str)->dict[str,Any]:
    b=_bek(); p=b.position_from_xgid(i) if i.startswith("XGID=") else b.position_from_gnuid(i); return p.to_dict()
def _canonical_safe(i:str)->dict[str,Any]|None:
    try:return _canonical(i)
    except Exception:return None
def _factual(i:str)->dict[str,Any]:
    v=json.loads(json.dumps(_canonical(i)));v.get("state",{}).pop("game_state",None);v.get("rules",{}).pop("maximum_cube",None);return v
def _same(a:str,b:str)->bool:
    try:return _factual(a)==_factual(b)
    except Exception:return False
def _flat(v:Any,p:str="")->dict[str,Any]:
    if not isinstance(v,dict):return {p:v}
    out={}
    for k,c in v.items():out.update(_flat(c,f"{p}.{k}" if p else str(k)))
    return out
def _diff(a:str,b:str)->list[dict[str,Any]]:
    try:x,y=_flat(_canonical(a)),_flat(_canonical(b))
    except Exception as exc:return [{"path":"decode","left":"","right":str(exc)}]
    return [{"path":p,"left":x.get(p),"right":y.get(p)} for p in sorted(set(x)|set(y)) if x.get(p)!=y.get(p)]

class NativeSurface:
    name="native_python"
    def xgid_to_gnuid(self,x):return str(_bek().xgid_to_gnuid(x,allow_lossy=True))
    def gnuid_to_xgid(self,g):return str(_bek().gnuid_to_xgid(g))
class BridgeSurface:
    name="engine_kit"
    def __init__(self):self.a=EngineKitResearchAdapter()
    def xgid_to_gnuid(self,x):
        r=self.a.xgid_to_gnuid(x)
        if not r.complete_gnuid:raise ValueError("Engine Kit public bridge returned no GNUID")
        return r.complete_gnuid
    def gnuid_to_xgid(self,g):
        r=self.a.gnuid_to_xgid(g)
        if not r.xgid:raise ValueError("Engine Kit public API returned no XGID")
        return r.xgid
class AnkiSurface:
    name="ankigammon_direct"
    def __init__(self):
        self.x=importlib.import_module("ankigammon.utils.xgid");self.g=importlib.import_module("ankigammon.utils.gnuid");self.m=importlib.import_module("ankigammon.models")
        try:self.version=importlib.metadata.version("ankigammon")
        except importlib.metadata.PackageNotFoundError:self.version="source-tree"
    def xgid_to_gnuid(self,xgid):
        p,m=self.x.parse_xgid(xgid);n=int(m.get("match_length",0))
        return str(self.g.encode_gnuid(p,cube_value=int(m.get("cube_value",1)),cube_owner=m.get("cube_owner",self.m.CubeState.CENTERED),dice=m.get("dice"),on_roll=m.get("on_roll",self.m.Player.X),score_x=int(m.get("score_x",0)),score_o=int(m.get("score_o",0)),match_length=n,crawford=bool(int(m.get("crawford_jacoby",0))&1) if n else False))
    def gnuid_to_xgid(self,gnuid):
        p,m=self.g.parse_gnuid(gnuid);n=int(m.get("match_length",0))
        return str(self.x.encode_xgid(p,cube_value=int(m.get("cube_value",1)),cube_owner=m.get("cube_owner",self.m.CubeState.CENTERED),dice=m.get("dice"),on_roll=m.get("on_roll",self.m.Player.O),score_x=int(m.get("score_x",0)),score_o=int(m.get("score_o",0)),match_length=n,crawford_jacoby=1 if n and bool(m.get("crawford")) else 0,max_cube=1024))

def _attempt(name:str,direction:str,source:str,reference:str,convert:Callable[[str],str],returner:Callable[[str],str])->dict[str,Any]:
    try:
        mid=convert(source);term=returner(mid);exact=mid==reference;semantic=_same(mid,reference)
        return {"surface":name,"label":LABELS[name],"direction":direction,"source":source,"middle":mid,"terminal":term,"status":"ok","error":None,"reference_exact":exact,"reference_semantic":semantic,"classification":"exact reference" if exact else ("semantic reference" if semantic else "state mismatch"),"roundtrip_exact":term==source,"roundtrip_semantic":_same(term,source),"middle_diff_from_reference":_diff(reference,mid),"roundtrip_diff_from_source":_diff(source,term)}
    except Exception as exc:
        return {"surface":name,"label":LABELS[name],"direction":direction,"source":source,"middle":None,"terminal":None,"status":"error","error":f"{type(exc).__name__}: {exc}","reference_exact":False,"reference_semantic":False,"classification":"error","roundtrip_exact":False,"roundtrip_semantic":False,"middle_diff_from_reference":[],"roundtrip_diff_from_source":[]}

def _gnu(gnu:Any,i:str,scratch:Path,key:str):
    if not i or i.startswith("XGID="):return None
    try:
        r=gnu.load(i,scratch,key);ex=split_exported_position(r.get("exported_text"));return {"complete_gnuid":r.get("complete_gnuid"),"board":ex.board,"details":ex.details,"rawboard":r.get("rawboard")}
    except Exception as exc:return {"error":f"{type(exc).__name__}: {exc}","board":"GNU CLI evidence unavailable."}
def _render(renderer:Any,i:str,d:Path,key:str):
    if not i or not i.startswith("XGID="):return None
    try:return renderer.render(i,d,key)
    except Exception as exc:return {"type":"unavailable","output":"","stderr":f"{type(exc).__name__}: {exc}"}
def _write_csv(path:Path,rows:list[dict[str,Any]]):
    fields=sorted({k for r in rows for k in r});
    with path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields);w.writeheader()
        for r in rows:w.writerow({k:r.get(k) for k in fields})

def build_gallery(*,cases_path:Path,output_dir:Path,r_library:Path,calculator=None,bglab=None,gnu=None,renderer=None)->dict[str,Any]:
    output_dir.mkdir(parents=True,exist_ok=True);renders=output_dir/"renders";gnu_dir=output_dir/"gnu-cli"
    calculator=calculator or BackgammonCalculatorReference();bglab=bglab or BglabGnuidOracle(r_library=r_library);gnu=gnu or GnuBackgammonCli();renderer=renderer or BackgammonBoardRenderer();surfaces=(NativeSurface(),BridgeSurface(),AnkiSurface())
    cases_out=[];comparisons=[];roundtrips=[];case_html=[]
    for case in load_cases(cases_path):
        dirs=[]
        for direction in ("XGID → GNUID → XGID","GNUID → XGID → GNUID"):
            x2g=direction.startswith("XGID");source=case.xgid if x2g else case.gnuid
            ref_mid=calculator.xgid_to_gnuid(source)["gnuid"] if x2g else calculator.gnuid_to_xgid(source)["xgid"]
            ref_term=calculator.gnuid_to_xgid(ref_mid)["xgid"] if x2g else calculator.xgid_to_gnuid(ref_mid)["gnuid"]
            gnu_post=None
            if x2g:
                try:gnu_post=gnu.load(source,gnu_dir,f"{case.case_id}-post").get("complete_gnuid")
                except Exception:pass
            bglab_out=None
            if not x2g:
                try:bglab_out=bglab.convert(source)["xgid"]
                except Exception as exc:bglab_out=f"unavailable: {exc}"
            attempts=[]
            for s in surfaces:
                c=s.xgid_to_gnuid if x2g else s.gnuid_to_xgid;r=s.gnuid_to_xgid if x2g else s.xgid_to_gnuid;a=_attempt(s.name,direction,source,ref_mid,c,r);attempts.append(a)
                comparisons.append({k:v for k,v in a.items() if k not in {"middle_diff_from_reference","roundtrip_diff_from_source"}}|{"case_id":case.case_id});roundtrips.append({"case_id":case.case_id,"direction":direction,"surface":s.name,"source":source,"middle":a.get("middle"),"terminal":a.get("terminal"),"exact":a["roundtrip_exact"],"semantic":a["roundtrip_semantic"]})
            ids={source,ref_mid,ref_term}|{a[k] for a in attempts for k in ("middle","terminal") if a.get(k)};rc={};gc={};cc={}
            for n,i in enumerate(sorted(ids)):
                rc[i]=_render(renderer,i,renders,f"{case.case_id}-{direction[0]}-{n}")
                gc[i]=_gnu(gnu,i,gnu_dir,f"{case.case_id}-{direction[0]}-{n}")
                cc[i]=_canonical_safe(i)
            def vis(a,b,c):return {"source_render":rc.get(a),"middle_render":rc.get(b),"terminal_render":rc.get(c),"source_gnu":gc.get(a),"middle_gnu":gc.get(b),"terminal_gnu":gc.get(c),"source_canonical":cc.get(a),"middle_canonical":cc.get(b),"terminal_canonical":cc.get(c),"reference_middle_canonical":cc.get(ref_mid)}
            ref=reference_card(direction,source,ref_mid,ref_term,vis(source,ref_mid,ref_term),bglab_output=bglab_out,gnu_post_import=gnu_post);methods=''.join(method_card(a,vis(source,a.get("middle") or source,a.get("terminal") or source)) for a in attempts)
            dirs.append(f'<section class="direction"><h3>{e(direction)}</h3>{ref}<div class="methods">{methods}</div></section>');cases_out.append({"case_id":case.case_id,"label":case.label,"direction":direction,"source":source,"reference_middle":ref_mid,"reference_terminal":ref_term,"gnu_post_import":gnu_post,"bglab_output":bglab_out,"methods":attempts})
        case_html.append(f'<section class="case"><h2>{e(case.case_id)} · {e(case.label)}</h2>{"".join(dirs)}</section>')
    provenance={"calculator":calculator.provenance,"bglab":bglab.provenance,"gnu":gnu.provenance,"renderer":renderer.provenance,"engine_kit":{"module":str(Path(_bek().__file__).resolve())},"ankigammon":{"version":getattr(surfaces[2],"version","unknown")}}
    report={"schema":"stable-player-oracle-first-gallery-v3","cases":cases_out,"comparisons":comparisons,"roundtrips":roundtrips,"provenance":provenance};(output_dir/"oracle-comparison-results.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");_write_csv(output_dir/"method-comparisons.csv",comparisons);_write_csv(output_dir/"roundtrips.csv",roundtrips);(output_dir/"oracle-gallery.html").write_text(render_page(case_html,provenance),encoding="utf-8");return report

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--cases",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--r-library",type=Path,required=True);a=p.parse_args();r=build_gallery(cases_path=a.cases,output_dir=a.output,r_library=a.r_library);hard=[x for x in r["comparisons"] if x["surface"] in {"native_python","engine_kit"} and not x["reference_semantic"]];return 1 if hard else 0
if __name__=="__main__":raise SystemExit(main())
