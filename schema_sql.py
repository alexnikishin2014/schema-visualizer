#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schema_sql.py — анализ схемы БД по MySQL-дампу.

Извлекает таблицы, столбцы, первичные/внешние ключи и индексы,
печатает DESC-структуру в формате mysql-клиента, дерево связей,
отчёт об анализе индексов и строит схему в Graphviz.

Выходные файлы называются по дампу: dump.dot + dump.png.
Заголовок на схеме — имя файла дампа. Размер PNG контролируется:
схема всегда помещается целиком (подбирается dpi).

Запуск:  py schema_sql.py [путь/к/дампу.sql]   (при запуске без аргумента имя спрашивается интерактивно)
Автор: Алексей Никишин
"""

import os
import re
import subprocess
import sys
from collections import OrderedDict

MODE = 'down'            # 'down' — от таблицы фактов вниз, к справочникам
                         # 'up'   — от справочников вверх, к таблицам фактов
BASE_DPI = 96            # базовое разрешение PNG
MAX_PNG_PX = 16000       # максимальная сторона PNG в пикселях (cairo в dot режет ~32767)

CREATE_RE = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?\s*\(', re.I)
ALTER_RE = re.compile(r'ALTER\s+TABLE\s+`?(\w+)`?\s+([^;]*?);', re.I | re.S)
FK_RE = re.compile(
    r'FOREIGN\s+KEY\s*\(\s*`?(\w+)`?\s*\)\s*REFERENCES\s+`?(\w+)`?\s*\(\s*`?(\w+)`?\s*\)', re.I)
IDX_RE = re.compile(
    r'\b(UNIQUE\s+(?:KEY|INDEX)|FULLTEXT\s+(?:KEY|INDEX)|KEY|INDEX)\s+`?(\w+)`?\s*\(([^)]*)\)', re.I)
PK_LINE_RE = re.compile(r'^(?:CONSTRAINT\s+`?\w+`?\s+)?PRIMARY\s+KEY\s*\(([^)]*)\)', re.I)
COL_RE = re.compile(r'^`?(\w+)`?\s+([A-Za-z]+(?:\s*\([^)]*\))?)', re.I)
DEFAULT_RE = re.compile(r"DEFAULT\s+('(?:[^'\\]|\\.)*'|NULL|[^\s,]+)", re.I)


def plural(n, one, few, many):
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def split_top(body):
    """Разбивает тело CREATE TABLE на части по запятым верхнего уровня."""
    parts, cur, depth = [], [], 0
    in_str = in_bt = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str:
            cur.append(ch)
            if ch == '\\' and i + 1 < len(body):
                cur.append(body[i + 1])
                i += 2
                continue
            if ch == "'":
                in_str = False
        elif in_bt:
            cur.append(ch)
            if ch == '`':
                in_bt = False
        elif ch == "'":
            in_str = True
            cur.append(ch)
        elif ch == '`':
            in_bt = True
            cur.append(ch)
        elif ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = ''.join(cur).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def clean_cols(raw):
    return [c.strip().strip('`') for c in raw.split(',') if c.strip()]


def choose_dpi(w_in, h_in, max_px=MAX_PNG_PX, base=BASE_DPI):
    """dpi, при котором схема гарантированно влезает в PNG целиком."""
    big = max(w_in, h_in)
    if big <= 0:
        return base
    return max(24, min(base, int(max_px / big)))


class Schema:
    def __init__(self, dump_name='schema.sql'):
        self.dump_name = dump_name
        base = os.path.splitext(dump_name)[0]
        self.dot_file = base + '.dot'
        self.png_file = base + '.png'
        self.tables = OrderedDict()
        self.fk_out = {}   # таблица -> [(столбец, целевая таблица)]
        self.fk_in = {}    # таблица -> [(ссылающаяся таблица, столбец)]

    # ---------- разбор дампа ----------

    def parse(self, sql):
        for m in CREATE_RE.finditer(sql):
            name = m.group(1)
            body = self._extract_body(sql, m.end() - 1)
            self.tables[name] = self._parse_body(body)
        # связи, добавленные через ALTER TABLE ... ADD FOREIGN KEY
        for m in ALTER_RE.finditer(sql):
            t = m.group(1)
            if t in self.tables:
                for fm in FK_RE.finditer(m.group(2)):
                    self.tables[t]['fks'].append((fm.group(1), fm.group(2)))
        for t, info in self.tables.items():
            for col, tgt in info['fks']:
                self.fk_out.setdefault(t, []).append((col, tgt))
                self.fk_in.setdefault(tgt, []).append((t, col))

    @staticmethod
    def _extract_body(sql, open_pos):
        """Тело CREATE TABLE — по глубине скобок, а не до первой ')'."""
        depth, i = 1, open_pos + 1
        while i < len(sql):
            ch = sql[i]
            if ch in "'`":
                q = ch
                i += 1
                while i < len(sql):
                    if sql[i] == '\\':
                        i += 2
                        continue
                    if sql[i] == q:
                        break
                    i += 1
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return sql[open_pos + 1:i]
            i += 1
        return sql[open_pos + 1:]

    def _parse_body(self, body):
        info = {'columns': [], 'indexes': [], 'fks': [], 'pk': []}
        for part in split_top(body):
            m = re.match(r'^\s*CONSTRAINT\s+`?\w+`?\s+(?:FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE)', part, re.I)
            if m:
                part = re.sub(r'^\s*CONSTRAINT\s+`?\w+`?\s+', '', part, flags=re.I)
            fm = FK_RE.search(part)
            if fm:
                info['fks'].append((fm.group(1), fm.group(2)))
                continue
            pm = PK_LINE_RE.match(part)
            if pm:
                info['pk'] = clean_cols(pm.group(1))
                continue
            im = IDX_RE.match(part)
            if im:
                kind = im.group(1).upper()
                info['indexes'].append({
                    'name': im.group(2),
                    'cols': clean_cols(im.group(3)),
                    'unique': kind.startswith('UNIQUE'),
                    'fulltext': kind.startswith('FULLTEXT'),
                    'primary': False,
                })
                continue
            cm = COL_RE.match(part)
            if cm:
                col = {'name': cm.group(1), 'type': cm.group(2),
                       'nullable': True, 'default': None,
                       'extra': '', 'pk': False, 'uni': False}
                up = part.upper()
                if re.search(r'\bPRIMARY\s+KEY\b', up):
                    col['pk'] = True
                if re.search(r'\bUNIQUE\b', up):
                    col['uni'] = True
                if re.search(r'\bAUTO_INCREMENT\b', up):
                    col['extra'] = 'auto_increment'
                if re.search(r'\bNOT\s+NULL\b', up):
                    col['nullable'] = False
                dm = DEFAULT_RE.search(part)
                if dm:
                    col['default'] = dm.group(1).strip("'")
                info['columns'].append(col)
        if not info['pk']:
            info['pk'] = [c['name'] for c in info['columns'] if c['pk']]
        pk_set = set(info['pk'])
        for c in info['columns']:
            if c['name'] in pk_set:
                c['nullable'] = False
        return info

    # ---------- справочные методы ----------

    def col_key(self, t, colname):
        info = self.tables[t]
        if colname in info['pk']:
            return 'PRI'
        if any(i['unique'] and colname in i['cols'] for i in info['indexes']):
            return 'UNI'
        col = next((c for c in info['columns'] if c['name'] == colname), None)
        if col and col['uni']:
            return 'UNI'
        if any(cc == colname for cc, _ in info['fks']):
            return 'MUL'
        if any(i['cols'] and i['cols'][0] == colname for i in info['indexes']):
            return 'MUL'
        return ''

    def is_isolated(self, t):
        return not self.fk_in.get(t) and not self.fk_out.get(t)

    def all_indexes(self, t):
        info = self.tables[t]
        idxs = list(info['indexes'])
        if info['pk']:
            idxs.insert(0, {'name': 'PRIMARY', 'cols': list(info['pk']),
                            'unique': True, 'fulltext': False, 'primary': True})
        # инлайн-UNIQUE на столбце — тоже уникальный индекс (MySQL создаёт его с именем столбца)
        names = {i['name'] for i in idxs}
        for c in info['columns']:
            if c['uni'] and c['name'] not in names:
                idxs.append({'name': c['name'], 'cols': [c['name']],
                             'unique': True, 'fulltext': False, 'primary': False})
                names.add(c['name'])
        return idxs

    def fk_without_index(self):
        """FK, для которого нет индекса с этим столбцом первым (или PK)."""
        out = []
        for t, info in self.tables.items():
            covered = {info['pk'][0]} if info['pk'] else set()
            for i in info['indexes']:
                if i['cols']:
                    covered.add(i['cols'][0])
            for col, tgt in info['fks']:
                if col not in covered:
                    out.append((t, col, tgt))
        return out

    def redundant_indexes(self):
        """Индексы, чьи столбцы — префикс столбцов другого индекса той же таблицы."""
        out = []
        for t in self.tables:
            idxs = self.all_indexes(t)
            for i in idxs:
                if i['primary'] or not i['cols']:
                    continue
                for j in idxs:
                    if j['name'] == i['name'] and j['primary'] == i['primary']:
                        continue
                    if j['cols'][:len(i['cols'])] == i['cols'] and len(j['cols']) > len(i['cols']):
                        out.append((t, i['name'], j['name'], list(i['cols'])))
                        break
        return out

    # ---------- печать ----------

    def print_foreign_keys(self):
        print()
        print('Найденные внешние ключи:')
        for t in self.tables:
            refs = self.fk_in.get(t)
            if not refs:
                continue
            cols, tgts = [], []
            for child, col in refs:
                if col not in cols:
                    cols.append(col)
                if child not in tgts:
                    tgts.append(child)
            keys = cols[0] if len(cols) == 1 else ', '.join(sorted(cols))
            print(f'- {t} связан ключом {keys} с: {sorted(tgts)}')

    def print_desc(self):
        print()
        print('Структура таблиц (DESC):')
        headers = ('Field', 'Type', 'Null', 'Key', 'Default', 'Extra')
        for t, info in self.tables.items():
            rows = []
            for c in info['columns']:
                rows.append((c['name'], c['type'],
                             'NO' if not c['nullable'] else 'YES',
                             self.col_key(t, c['name']),
                             c['default'] if c['default'] is not None else 'NULL',
                             c['extra']))
            widths = []
            for i in range(len(headers)):
                w = len(headers[i])
                for r in rows:
                    w = max(w, len(r[i]))
                widths.append(w)
            sep = '+' + '+'.join('-' * (w + 2) for w in widths) + '+'
            fmt = lambda r: '|' + '|'.join(
                ' {} '.format(r[i].ljust(widths[i])) for i in range(len(headers))) + '|'
            print()
            print(f'  Таблица {t}:')
            print('  ' + sep)
            print('  ' + fmt(headers))
            print('  ' + sep)
            for r in rows:
                print('  ' + fmt(r))
            print('  ' + sep)

    def print_forest(self, mode=MODE):
        print()
        if mode == 'down':
            print('Дерево связей (направление: от таблицы фактов вниз, к справочникам):')
            roots = [t for t in self.tables if not self.fk_in.get(t)]
        else:
            print('Дерево связей (направление: от справочников вверх, к таблицам фактов):')
            roots = [t for t in self.tables if not self.fk_out.get(t)]
        shown = set()

        def children(t):
            if mode == 'down':
                return sorted({tgt for _, tgt in self.fk_out.get(t, [])})
            return sorted({child for child, _ in self.fk_in.get(t, [])})

        def subtree_size(t, seen=None):
            seen = seen if seen is not None else set()
            if t in seen:
                return 0
            seen.add(t)
            return 1 + sum(subtree_size(k, seen) for k in children(t))

        roots.sort(key=lambda t: -subtree_size(t))

        def rec(t, prefix, ext, path, lines):
            if t in path:
                lines.append(f'{prefix}{ext}{t} (цикл: уже показана в этой ветке)')
                return
            if self.is_isolated(t):
                suffix = ' (сирота, нет связей с другими таблицами)'
            elif t in shown:
                suffix = ' (уже показана выше)'
            else:
                suffix = ''
            lines.append(f'{prefix}{ext}{t}{suffix}')
            shown.add(t)
            kids = children(t)
            for i, k in enumerate(kids):
                last = i == len(kids) - 1
                e = '\\__ ' if last else '|-- '
                child_prefix = prefix + ('    ' if ext == '\\__ ' else ('|   ' if ext == '|-- ' else ''))
                rec(k, child_prefix, e, path | {t}, lines)

        blocks = []
        for r in roots:
            lines = []
            rec(r, '', '', set(), lines)
            blocks.append('\n'.join(lines))
        leftovers = [t for t in self.tables if t not in shown]
        if leftovers:
            blocks.append('Компоненты без корня (циклы):')
            for t in leftovers:
                if t in shown:
                    continue
                lines = []
                rec(t, '', '', set(), lines)
                blocks.append('\n'.join(lines))
        print('\n\n'.join(blocks))

    def print_index_report(self):
        print()
        print('ОТЧЁТ ОБ АНАЛИЗЕ ИНДЕКСОВ')
        print('=' * 60)
        total = uniq = full = 0
        for t in self.tables:
            idxs = self.all_indexes(t)
            n = len(idxs)
            u = sum(1 for i in idxs if i['unique'] and not i['primary'])
            f = sum(1 for i in idxs if i['fulltext'])
            total, uniq, full = total + n, uniq + u, full + f
            print(f'{t}: {n} {plural(n, "индекс", "индекса", "индексов")}')
            for i in idxs:
                if i['primary']:
                    desc = 'первичный ключ'
                elif i['unique']:
                    desc = 'уникальный'
                elif i['fulltext']:
                    desc = 'полнотекстовый'
                else:
                    desc = 'обычный'
                print(f'    {i["name"]} ({", ".join(i["cols"])}) — {desc}')
        print()
        print(f'Итого индексов: {total} (уникальных: {uniq}, полнотекстовых: {full})')
        print()
        print('Проверки:')
        problems = 0
        for t, col, tgt in self.fk_without_index():
            print(f'  - {t}: FK ({col}) -> {tgt} не имеет индекса — '
                  f'JOIN и DELETE по {tgt} пойдут полным сканированием')
            problems += 1
        for t, name, cover, cols in self.redundant_indexes():
            print(f'  - {t}: индекс {name} избыточен, его покрывает {cover} '
                  f'(префикс {", ".join(cols)})')
            problems += 1
        if not problems:
            print('  - проблем не найдено')

    # ---------- Graphviz ----------

    def export_dot(self, mode=MODE):
        problems = {t for t, _, _ in self.fk_without_index()}
        if mode == 'down':
            roots = {t for t in self.tables if not self.fk_in.get(t)}
        else:
            roots = {t for t in self.tables if not self.fk_out.get(t)}
        lines = [f'digraph "{esc(self.dump_name)}" {{',
                 '    rankdir=TB;',
                 f'    label="{esc(self.dump_name)}";',
                 '    labelloc="t";',
                 '    fontsize=22;',
                 '    node [shape=plaintext, nodesep=0.4, ranksep=0.6];',
                 '    edge [fontsize=10];']
        for t, info in self.tables.items():
            hdr = '#f5a623' if t in roots else '#4a76a8'
            parts = ['<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">']
            parts.append('<TR><TD BGCOLOR="%s"><FONT COLOR="white"><B>%s</B></FONT></TD></TR>'
                         % (hdr, esc(t)))
            for c in info['columns']:
                key = self.col_key(t, c['name'])
                is_fk = any(cc == c['name'] for cc, _ in info['fks'])
                if key == 'PRI':
                    prefix, color, bold = 'PK', '#b03a2e', True
                elif key == 'UNI':
                    prefix, color, bold = 'UNI', '#6c3483', False
                elif key == 'MUL' and is_fk:
                    prefix, color, bold = 'FK', '#d35400', False
                elif key == 'MUL':
                    prefix, color, bold = 'IDX', '#2874a6', False
                else:
                    prefix, color, bold = '', '#566573', False
                b1, b2 = ('<B>', '</B>') if bold else ('', '')
                text = (prefix + ' ' if prefix else '') + esc(c['name']) + ' ' + esc(c['type'])
                parts.append('<TR><TD ALIGN="LEFT"><FONT COLOR="%s" POINT-SIZE="10">%s%s%s</FONT></TD></TR>'
                             % (color, b1, text, b2))
            parts.append('</TABLE>')
            attr = 'label=<' + ''.join(parts) + '>'
            if t in problems:
                attr += ', color="#f1c40f", penwidth=2'
            lines.append('    %s [%s];' % (t, attr))
        for t, info in self.tables.items():
            for col, tgt in info['fks']:
                lines.append('    %s -> %s [label="%s"];' % (t, tgt, esc(col)))
        lines.append('}')
        with open(self.dot_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print()
        print(f'DOT сохранён: {self.dot_file}')

    def render_png(self):
        # измеряем натуральный размер схемы и подбираем dpi так,
        # чтобы PNG гарантированно вместился целиком (cairo в dot
        # обрезает картинки больше ~32767 пикселей по стороне)
        dpi = BASE_DPI
        try:
            p = subprocess.run(['dot', '-Tplain', self.dot_file],
                               capture_output=True, text=True, check=True)
            last = p.stdout.strip().splitlines()
            if last:
                parts = last[-1].split()
                if len(parts) >= 4 and parts[0] == 'graph':
                    w, h = float(parts[2]), float(parts[3])
                    dpi = choose_dpi(w, h)
                    if dpi < BASE_DPI:
                        print(f'Схема большая ({w:.0f}x{h:.0f} дюймов) — '
                              f'масштабирую до {dpi} dpi, чтобы PNG вместился целиком')
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        try:
            subprocess.run(['dot', '-Tpng', f'-Gdpi={dpi}', self.dot_file, '-o', self.png_file], check=True)
            print(f'PNG сохранён: {self.png_file}')
        except FileNotFoundError:
            print('Graphviz не найден — установите: sudo apt install graphviz')
            print(f'DOT-файл сохранён, отрисовать можно позже: dot -Tpng {self.dot_file} -o {self.png_file}')
        except subprocess.CalledProcessError:
            print(f'Graphviz не смог отрисовать {self.dot_file} — проверьте синтаксис DOT')


def prompt_dump_file():
    # прощающий режим чтения: терминал может отдавать не-UTF-8 байты
    if sys.stdin and hasattr(sys.stdin, 'reconfigure'):
        try:
            sys.stdin.reconfigure(errors='replace')
        except Exception:
            pass
    sqls = sorted(f for f in os.listdir('.') if f.lower().endswith('.sql'))
    while True:
        try:
            name = input('Введите имя файла дампа: ').strip()
        except UnicodeDecodeError:
            print('Не удалось прочитать ввод (проблема кодировки) — введите имя ещё раз.')
            continue
        except EOFError:
            print('\nВвод прерван. Можно запустить скрипт с аргументом: py schema_sql.py dump.sql')
            return None
        if not name:
            print('Имя не введено. Доступные SQL-файлы в текущей папке: '
                  + (', '.join(sqls) if sqls else 'нет'))
            continue
        if not os.path.splitext(name)[1]:
            name += '.sql'
        if os.path.exists(name):
            return name
        print(f'Файл не найден: {name}')
        print('Доступные SQL-файлы в текущей папке: ' + (', '.join(sqls) if sqls else 'нет'))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else prompt_dump_file()
    if not path:
        return
    if not os.path.splitext(path)[1]:
        path += '.sql'
    if not os.path.exists(path):
        print(f'Файл не найден: {path}')
        return
    with open(path, encoding='utf-8', errors='replace') as f:
        sql = f.read()
    schema = Schema(dump_name=os.path.basename(path))
    schema.parse(sql)
    print(f'Найдено таблиц: {len(schema.tables)}')
    schema.print_foreign_keys()
    schema.print_desc()
    schema.print_forest(mode=MODE)
    schema.print_index_report()
    schema.export_dot(mode=MODE)
    schema.render_png()


if __name__ == '__main__':
    main()
