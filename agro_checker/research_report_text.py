"""Presentation only: paragraph spacing and Russian number spacing."""
import re


def markdown(lines):
    out=[];fenced=False
    for i,line in enumerate(lines):
        if line.startswith('```'):fenced=not fenced
        if not fenced and not line.startswith('|'):
            line=re.sub(r'([А-Яа-яЁё])(?=\d)',r'\1 ',line)
            line=re.sub(r'(\d)(?=[А-Яа-яЁё])',r'\1 ',line)
        out.append(line)
        if i+1<len(lines) and line and lines[i+1] and not fenced and not line.startswith(('#','|','```')) and not lines[i+1].startswith(('#','|','```')):out.append('')
    return '\n'.join(out)+'\n'
