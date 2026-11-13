"""Read-only operational checks; optional dated health receipt, never train or issue."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agro_checker import prospective_scouting as s
from scripts.verify_prospective_scouting import verify


def scheduler():
    if os.name!='nt':return dict(status='not_checked',reason='Windows scheduler is local to the pilot laptop')
    command="""$t=Get-ScheduledTask -TaskName 'Onlyfarms-ProspectiveCheck' -ErrorAction Stop;
    $i=Get-ScheduledTaskInfo -TaskName $t.TaskName -ErrorAction Stop;
    [ordered]@{name=$t.TaskName;state=[string]$t.State;last_result=$i.LastTaskResult;
    last_run_utc=$i.LastRunTime.ToUniversalTime().ToString('o');next_run_utc=$i.NextRunTime.ToUniversalTime().ToString('o')}|ConvertTo-Json -Compress"""
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=30)
    if result.returncode:return dict(status='unavailable',reason='Scheduled task could not be read')
    value=json.loads(result.stdout);value['status']='checked';return value


def operational_checks(at,end,task,report,replay):
    last=s.instant(task['last_run_utc']) if task.get('last_run_utc') else None
    return dict(frozen_release_replay=replay['releases_verified']==report['releases'],
        windows_task_available=task['status']=='checked',
        windows_task_enabled=task.get('state') in ('Ready','Running'),
        last_scheduled_run_succeeded=task.get('last_result')==0,
        last_scheduled_run_recent=last is not None and 0<=(at-last).total_seconds()<48*3600,
        catalogue_period_recent=0<=(at-end).total_seconds()<48*3600,
        pending_not_used_as_quality=not(report['finalised']==0 and report['available_quality_evidence']))


def health():
    at=datetime.now(timezone.utc);settings,protocol_sha=s.configuration();replay=verify()
    pointer=s.read(s.satellite.DIRECTORY/'current.json');archive=s.satellite.DIRECTORY/pointer['directory']
    p=s.read(archive/'protocol.json');end=s.instant(p['periods'][0]['end'])
    task=scheduler();report=s.report()
    # snapshot validates the published pointer and all index-crop source hashes.
    s.snapshot(at)
    checks=operational_checks(at,end,task,report,replay)
    return dict(checked_at_utc=at.isoformat(),protocol_sha256=protocol_sha,checks=checks,
        healthy=all(checks.values()),scheduler=task,archive_version=archive.name,
        archive_end_exclusive_utc=end.isoformat(),replay=replay,
        releases=report['releases'],pending=report['pending'],finalised=report['finalised'],
        quality_evidence_available=report['available_quality_evidence'],
        model_training_performed=False,predictions_written=0,human_visits_created=0,
        limitation='Task requires powered-on logged-in Windows laptop and internet; health receipt is not continuous uptime proof.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--save',action='store_true');args=parser.parse_args()
    value=health()
    if args.save:
        s.immutable(s.DIRECTORY/'operations'/('health-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json'),value)
    print(json.dumps(value,ensure_ascii=False,indent=2));sys.exit(0 if value['healthy'] else 1)
