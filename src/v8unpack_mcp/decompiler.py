"""Декомпилятор байт-кода 1С (`image`) → BSL.

Порт алгоритма из открытого проекта 1C-inversion (форк EvilBeaver/v8asm, MIT).
Восстанавливает исходники Процедур/Функций закрытого (зашифрованного) модуля
из его компилированного байт-кода. Комментарии и форматирование не восстанавливаются
(платформа их удаляет при закрытии модуля).

Локальные переменные закрытого модуля хранятся в байт-коде как GUID-имена вида
`x<hex>`, поэтому при декомпиляции им присваиваются читаемые имена `ПеременнаяN`.

⚠️ ВНИМАНИЕ. Декомпилятор добавлен в исследовательских целях и НЕ должен
использоваться для взлома или снятия защиты чужих конфигураций — это нарушает
лицензию 1С и авторское право (ст. 146 УК РФ). Используйте только для
восстановления собственных модулей (потеря исходника, починка после смены
платформы). Использование — на свой риск.
"""

from .bytecode import parse_image, opcodes as _raw_opcodes


# --- Флаги процедур (ФлагиПроцедуры) ---
FLAG_FUNCTION = 1
FLAG_EXPORT = 16
FLAG_EXTERNAL = 32

# --- Приоритеты операторов (ДекомпиляцияНаСервере) ---
_OPERATOR_PRIORITY = {
    23: 0,   # унарный минус
    24: 2,   # +
    25: 2,   # -
    26: 1,   # *
    27: 1,   # /
    28: 1,   # %
    29: 3,   # Не
    30: 5,   # И
    31: 6,   # ИЛИ
    33: 4,   # =
    34: 4,   # <>
    35: 4,   # >
    36: 4,   # <
    37: 4,   # >=
    38: 4,   # <=
}

# --- Встроенные функции (опкоды 53-128) ---
# arity = -1 означает переменное число аргументов (Мин/Макс)
_BUILTINS = {
    53: ("СтрДлина", 1), 54: ("СокрЛ", 1), 55: ("СокрП", 1), 56: ("СокрЛП", 1),
    57: ("Лев", 2), 58: ("Прав", 2), 59: ("Сред", 3), 60: ("Найти", 2),
    61: ("Врег", 1), 62: ("Нрег", 1), 63: ("Символ", 1), 64: ("КодСимвола", 2),
    65: ("ПустаяСтрока", 1), 66: ("Цел", 1), 67: ("Окр", 2), 68: ("Окр", 3),
    70: ("Число", 1), 71: ("Строка", 1), 72: ("Дата", 1), 73: ("Дата", 3),
    74: ("Дата", 6), 75: ("ДобавитьМесяц", 2), 76: ("НачалоМесяца", 1),
    77: ("КонецМесяца", 1), 78: ("НачалоКвартала", 1), 79: ("КонецКвартала", 1),
    80: ("НачалоГода", 1), 81: ("КонецГода", 1), 82: ("Год", 1), 83: ("Месяц", 1),
    84: ("День", 1), 85: ("Час", 1), 86: ("Минута", 1), 87: ("Секунда", 1),
    88: ("ДеньГода", 1), 89: ("НеделяГода", 1), 90: ("ДеньНедели", 1),
    91: ("НачалоНедели", 1), 92: ("КонецНедели", 1), 93: ("НачалоДня", 1),
    94: ("КонецДня", 1), 95: ("НачалоЧаса", 1), 96: ("КонецЧаса", 1),
    97: ("НачалоМинуты", 1), 98: ("КонецМинуты", 1), 99: ("ТекущаяДата", 0),
    100: ("СтрЗаменить", 3), 101: ("СтрЧислоСтрок", 1), 102: ("СтрПолучитьСтроку", 2),
    103: ("Мин", -1), 104: ("Макс", -1), 105: ("СтрЧислоВхождений", 2),
    106: ("ОписаниеОшибки", 0), 107: ("ТипЗнч", 1), 108: ("Тип", 1),
    109: ("Вычислить", 1), 110: ("Формат", 2), 111: ("Новый", 2),
    112: ("ACos", 1), 113: ("ASin", 1), 114: ("ATan", 1), 115: ("Cos", 1),
    116: ("Exp", 1), 117: ("Log", 1), 118: ("Log10", 1), 119: ("Pow", 2),
    120: ("Sin", 1), 121: ("Sqrt", 1), 122: ("Tan", 1), 127: ("ТРег", 1),
    128: ("ИнформацияОбОшибке", 0),
}


def _unq(s):
    if isinstance(s, str) and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def deserialize_string(s):
    """ДесериализоватьСтроку: раскрыть 1С-строку ("" -> ", снять кавычки)."""
    if isinstance(s, str) and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('""', '"')
    return s


def string_to_bsl(s):
    """ПолучитьПредставлениеПоТипу для строки: экранировать и обернуть в кавычки."""
    s = s.replace("\n", "\n|")
    s = s.replace('"', '""')
    return '"' + s + '"'


def _const_to_bsl(kind, value):
    if kind == "S":
        return string_to_bsl(value)
    if kind == "D":
        v = value.replace("\xa0", "")
        if len(v) == 14 and v[8:] == "000000":
            v = v[:8]
        return "'" + v + "'"
    if kind == "N":
        return value.replace("\xa0", "").replace(",", ".")
    if kind == "B":
        return "Истина" if value == "1" else "Ложь"
    if kind == "L":
        return "NULL"
    if kind == "U":
        return "Неопределено"
    return value


class _Method:
    __slots__ = ("name", "flags", "argc", "entry", "vars", "defaults", "labels")

    def __init__(self, name, flags, argc, entry):
        self.name = name
        self.flags = flags
        self.argc = argc
        self.entry = entry
        self.vars = []       # list of (name, flags)
        self.defaults = []   # list of (kind, value)
        self.labels = []     # list of (name, op_index)


class Decompiler:
    """Декомпилятор одного модуля по разобранному `image`."""

    def __init__(self, image):
        self._parse_operations(image["cmd"])
        self._parse_constants(image["const"])
        self._parse_globals(image["var"])
        self._parse_methods(image["proc"])

        self.stack = [""] * 256
        self.top = -1
        self.text = ""
        self.blocks = {}     # индекс операции -> текст конца блока
        self.labels = {}     # индекс операции -> имя метки
        self.exceptions = set()  # индексы операций "Исключение"
        self.idx = 0
        self.ops_count = len(self.ops)
        # Новый компилятор (8.3.24+) использует JmpTrue (41) и граф условных переходов
        # вместо структурированного тернарника; в этом случае не разворачиваем тернарник.
        self._has_jmptrue = any(o[0] == 41 for o in self.ops)

    # --- Разбор секций ---

    def _parse_operations(self, cmd):
        start = 3 if (len(cmd) >= 3 and cmd[0] == '"Cmd"') else 0
        self.entry_point = int(_unq(cmd[2])) if len(cmd) > 2 else 0
        self.ops = []
        for o in cmd[start:]:
            if isinstance(o, list) and len(o) >= 2:
                self.ops.append([int(_unq(o[0])), int(_unq(o[1]))])

    def _parse_constants(self, const):
        start = 2 if (len(const) >= 2 and const[0] == '"Const"') else 0
        self.consts = []
        for c in const[start:]:
            if isinstance(c, list) and len(c) >= 2:
                kind = _unq(c[0])
                value = deserialize_string(c[1]) if kind == "S" else c[1]
                self.consts.append((kind, value))

    def _parse_globals(self, var):
        start = 2 if (len(var) >= 2 and var[0] == '"Var"') else 0
        self.globals = []
        for v in var[start:]:
            if isinstance(v, list) and len(v) >= 2:
                self.globals.append((_unq(v[0]), int(_unq(v[1]))))

    def _parse_methods(self, proc):
        self.methods = []
        for entry in proc:
            if not isinstance(entry, list) or len(entry) < 4:
                continue
            name = _unq(entry[0])
            if not isinstance(name, str) or not name:
                continue
            flags = entry[1]
            if not isinstance(flags, str) or not flags.isdigit():
                continue
            m = _Method(name, int(flags), int(_unq(entry[2])), int(_unq(entry[3])))
            for sub in entry[4:]:
                if not isinstance(sub, list) or not sub:
                    continue
                tag = _unq(sub[0])
                if tag == "Var":
                    for v in sub[2:]:
                        if isinstance(v, list) and len(v) >= 2:
                            m.vars.append((_unq(v[0]), int(_unq(v[1]))))
                elif tag == "DefPrm":
                    for v in sub[2:]:
                        if isinstance(v, list) and len(v) >= 1:
                            kind = _unq(v[0])
                            value = deserialize_string(v[1]) if (kind == "S" and len(v) >= 2) else (v[1] if len(v) >= 2 else "")
                            m.defaults.append((kind, value))
                elif tag == "Lbl":
                    for v in sub[2:]:
                        if isinstance(v, list) and len(v) >= 2:
                            m.labels.append((_unq(v[0]), int(_unq(v[1]))))
            self.methods.append(m)

    # --- Стек ---

    def _push(self, code, offset=0):
        self.top += offset
        if self.top >= len(self.stack):
            self.stack.extend([""] * (self.top - len(self.stack) + 1))
        self.stack[self.top] = code

    def _pop(self):
        if self.top < 0:
            return ""
        code = self.stack[self.top]
        self.top -= 1
        return code

    def _end_line(self, code, ignore):
        if ignore:
            return code
        if self.idx + 1 >= self.ops_count:
            return code
        nxt = self.ops[self.idx + 1][0]
        if nxt in (1, 39, 44, 45):
            code += ";"
        return code

    def _fix_block(self, index):
        if index in self.blocks:
            self.text += "\n\n" + self.blocks[index]

    def _fix_label(self, index):
        if index in self.labels:
            self.text += "\n\n" + self.labels[index] + ":"

    def _flush_stack(self):
        for i in range(self.top + 1):
            if self.text:
                self.text += "\n\n" + self.stack[i]
            else:
                self.text = self.stack[i]
        self.top = -1

    # --- Получение имени переменной ---

    def _local_var(self, m, index):
        if 0 <= index < len(m.vars):
            name = m.vars[index][0]
            if name.startswith("x"):
                return f"Переменная{index}"
            return name
        return f"Переменная{index}"

    def _global_var(self, index):
        if 0 <= index < len(self.globals):
            name = self.globals[index][0]
            if name.startswith("x"):
                return f"Переменная{index}"
            return name
        return f"Переменная{index}"

    # --- Операторные скобки ---

    def _fix_prev_operand_parentheses(self, op):
        # УстановитьСкобкиПредыдущимЧленам (упрощённо)
        cur_prio = _OPERATOR_PRIORITY.get(op)
        if cur_prio is None or self.top < 1:
            return
        prev_op = self.ops[self.idx - 1][0] if self.idx > 0 else -1
        prev_prio = _OPERATOR_PRIORITY.get(prev_op)
        if prev_prio is not None and prev_prio > cur_prio:
            s = self.stack[self.top - 1]
            if any(c in s for c in "+-*/%"):
                self.stack[self.top - 1] = "(" + s + ")"

    # --- Основной цикл декомпиляции тела метода ---

    def decompile_method_body(self, m):
        self.stack = [""] * 256
        self.top = -1
        self.text = ""
        self.blocks = {}
        self.labels = {op: name for name, op in m.labels}
        self._build_exceptions(m)

        self.idx = m.entry
        last = self._find_method_end(m)
        while self.idx <= last:
            self._process_opcode(m, last, ignore_end=False)
            self.idx += 1

        if self.top > -1:
            self._flush_stack()
        return self.text

    def _find_method_end(self, m):
        i = m.entry
        while i < self.ops_count:
            if self.ops[i][0] == 22:  # End
                return i
            i += 1
        return self.ops_count - 1

    def _is_function(self, m):
        i = m.entry
        while i < self.ops_count:
            if self.ops[i][0] == 17:
                return True
            if self.ops[i][0] == 22:
                return False
            i += 1
        return False

    def _build_exceptions(self, m):
        # Собираем блоки «Попытка» как [начало, конец]. Опкод 44 = Попытка,
        # 46 = КонецПопытки. Блоки могут быть вложенными.
        self.exceptions = set()
        blocks = []  # (start, end)
        i = m.entry
        last = self._find_method_end(m)
        pending = []
        while i <= last:
            op = self.ops[i][0]
            if op == 44:
                pending.append(i)
            elif op == 46 and pending:
                start = pending.pop()
                blocks.append((start, i))
            i += 1
        for bs, be in blocks:
            # Блок вложен, если его целиком охватывает другой блок Попытка.
            nested = any(bs2 < bs and be < be2 for bs2, be2 in blocks)
            matches = []
            for j in range(bs, be + 1):
                if self.ops[j][0] != 45 or self.ops[j][1] != 1:
                    continue
                if j > 0 and self.ops[j - 1][0] == 17:
                    continue  # это «Возврат», а не «Исключение»
                # Исключение из вложенного блока Попытка текущему блоку не подходит.
                if any(bs < bs2 and be2 <= be and bs2 <= j <= be2 for bs2, be2 in blocks):
                    continue
                matches.append(j)
            if not matches:
                continue
            # У внешнего блока берём последнее подходящее «Исключение»,
            # у вложенного — первое (как в 1C-inversion).
            self.exceptions.add(matches[-1] if not nested else matches[0])

    # --- Обработка одной операции ---

    def _process_opcode(self, m, last, ignore_end):
        op = self.ops[self.idx][0]
        arg = self.ops[self.idx][1]

        if op == 0:
            pass
        elif op == 1:  # LineNum: конец строки + детект цикла "Пока"
            if self.top > -1:
                if self.ops[self.idx + 1][0] == 40:
                    jmp = self.ops[self.idx + 1][1]
                    if jmp > 0 and self.ops[jmp - 1][1] < self.idx:
                        # цикл "Пока Условие Цикл"
                        block_end = self.blocks.get(jmp)
                        if block_end is None:
                            self.blocks[jmp] = "КонецЦикла;"
                        else:
                            self.blocks[jmp] = "КонецЦикла;\n\n" + block_end
                        code = "Пока " + self.stack[self.top] + " Цикл"
                        self._push(code, 0)
                self._flush_stack()
        elif op == 2:  # LdVar
            self._push(self._global_var(arg), 1)
        elif op == 3:  # LdLoc
            self._push(self._local_var(m, arg), 1)
        elif op == 4:  # LdConst
            kind, value = self.consts[arg]
            self._push(_const_to_bsl(kind, value), 1)
        elif op == 5:  # LdRet — результат вызова; следующий LineNum пропускаем
            self._fix_block(self.idx)
            self._fix_label(self.idx)
            self.idx += 1
        elif op == 6:
            self._push("Ложь", 1)
        elif op == 7:
            self._push("Истина", 1)
        elif op == 8:
            self._push("Неопределено", 1)
        elif op == 9:
            self._push("NULL", 1)
        elif op == 10:
            self._push("", 1)
        elif op in (11, 14, 15, 49):
            # 11/14/15/49 — служебные опкоды (Iter/Next/LdTmp механики «Для каждого»).
            # Поглощаются контекстом цикла (_handle_for_loop); сюда попадают только при
            # рекурсивной обработке условных переходов нового компилятора — пропускаем.
            pass
        elif op == 32:  # Булево (то же, что op 69) — свернуть a И/ИЛИ b в инфикс
            self.stack[self.top - 2] = f"{self.stack[self.top - 2]} {self.stack[self.top - 1]} {self.stack[self.top]}"
            self.top -= 2
        elif op == 12:  # получение свойства
            self.stack[self.top] = self.stack[self.top] + "." + self.consts[arg][1]
        elif op == 13:  # получение элемента массива []
            code = self.stack[self.top - 1] + "[" + self.stack[self.top] + "]"
            self._push(code, -1)
        elif op == 16:  # присваивание =
            code = self.stack[self.top - 1] + " = " + self.stack[self.top]
            code = self._end_line(code, ignore_end)
            self._push(code, -1)
        elif op == 17:  # Возврат
            code = "Возврат " + self.stack[self.top]
            code = self._end_line(code, ignore_end)
            self._push(code, 0)
            self._fix_block(self.idx)
            self._fix_label(self.idx)
            self._fix_block(self.idx + 1)
            self._fix_label(self.idx + 1)
            self.idx += 2
        elif op == 18:  # ArgNum (обрабатывается следующим вызовом)
            pass
        elif op == 19:  # CallLoc — вызов локальной процедуры/функции
            name = self.methods[arg].name
            argc = self.ops[self.idx - 1][1]
            args = [self._pop() for _ in range(argc)][::-1]
            code = f"{name}({', '.join(args)})"
            code = self._end_line(code, ignore_end)
            self._push(code, 1)
        elif op in (20, 21):  # вызов метода объекта .Имя()
            name = self.consts[arg][1]
            argc = self.ops[self.idx - 1][1]
            args = [self._pop() for _ in range(argc)][::-1]
            obj = self._pop()
            code = f"{obj}.{name}({', '.join(args)})"
            code = self._end_line(code, ignore_end)
            self._push(code, 1)
        elif op == 22:  # End
            return
        elif op == 23:  # унарный минус
            self.stack[self.top] = "-" + self.stack[self.top]
        elif op in (24, 25, 26, 27, 28, 33, 34, 35, 36, 37, 38):
            self._fix_prev_operand_parentheses(op)
            sym = {24: "+", 25: "-", 26: "*", 27: "/", 28: "%",
                   33: "=", 34: "<>", 35: ">", 36: "<", 37: ">=", 38: "<="}[op]
            code = f"{self.stack[self.top - 1]} {sym} {self.stack[self.top]}"
            self._push(code, -1)
        elif op == 29:  # Не
            code = "Не " + self.stack[self.top]
            self._push(code, 0)
        elif op == 30:  # И (сокращённое)
            self._push("И", 1)
        elif op == 31:  # ИЛИ
            self._push("ИЛИ", 1)
        elif op == 39:  # Jmp — Иначе / Возврат / Продолжить / Прервать
            self._handle_jmp(m, last)
        elif op == 40:  # JmpFalse — Если / Пока / ?(...)
            self._handle_jmp_false(m, last, ignore_end)
        elif op == 41:  # JmpTrue — переход, если Истина (новый компилятор 8.3.24+)
            # Полное восстановление структуры требует графа переходов (условные переходы
            # вместо структурированных И/ИЛИ). Пока пропускаем переход, условие остаётся
            # на стеке и выводится на ближайшем LineNum — получаем линейный читаемый код.
            pass
        elif op == 42:  # Перейти Метка
            lbl = next((n for n, o in m.labels if o == arg), f"Метка{arg}")
            self.text += "\n\nПерейти " + lbl + ";"
        elif op == 43:  # Инкремент (невидим)
            self.top -= 1
        elif op == 44:  # Попытка
            self._push("Попытка", 1)
        elif op == 45:  # Возврат/Исключение
            if self.idx in self.exceptions:
                self._push("Исключение", 1)
                self._fix_block(self.idx)
                self._fix_label(self.idx)
                self.idx += 1
        elif op == 46:  # КонецПопытки
            self._push("КонецПопытки;", 1)
        elif op == 47:  # ВызватьИсключение
            code = "ВызватьИсключение"
            if arg == 1:
                code += " " + self._pop()
            code = self._end_line(code, ignore_end)
            self._push(code, 1)
        elif op == 48:  # PutTmp — цикл "Для"
            self._handle_for_loop(m)
        elif op == 50:  # PopTmp — конец цикла; пропускаем служебную очистку
            if self.idx + 6 < self.ops_count:
                p = [self.ops[self.idx + k][0] for k in range(1, 7)]
                if p == [3, 8, 16, 3, 8, 16]:
                    for k in range(6):
                        self._fix_block(self.idx + k)
                        self._fix_label(self.idx + k)
                    self.idx += 6
        elif op == 51:  # Новый
            name = self.consts[arg][1]
            argc = self.ops[self.idx - 1][1]
            args = [self._pop() for _ in range(argc)][::-1]
            code = f"Новый {name}({', '.join(args)})" if args else f"Новый {name}"
            self._push(code, 1)
        elif op == 52:  # Выполнить
            self.stack[self.top] = f"Выполнить({self.stack[self.top]});"
        elif op == 69:  # Булево (инфикс a op b)
            self.stack[self.top - 2] = f"{self.stack[self.top - 2]} {self.stack[self.top - 1]} {self.stack[self.top]}"
            self.top -= 2
        elif op in _BUILTINS:
            name, arity = _BUILTINS[op]
            if arity == -1:
                argc = self.ops[self.idx - 1][1]
                args = [self._pop() for _ in range(argc)][::-1]
                self._push(f"{name}({', '.join(args)})", 1)
            else:
                # фиксированная арность: читаем аргументы БЕЗ выталкивания,
                # затем сжимаем стек на (арность - 1) и кладём результат на вершину
                args = [self.stack[self.top - arity + 1 + i] for i in range(arity)]
                code = f"{name}({', '.join(args)})"
                self.top -= (arity - 1)
                self.stack[self.top] = code
        elif op in (123, 124, 125, 126):  # Добавить/УдалитьОбработчик
            pass
        else:
            raise ValueError(f"Не предусмотрен КодОперации={op}")

        self._fix_block(self.idx)
        self._fix_label(self.idx)

    def _handle_jmp(self, m, last):
        arg = self.ops[self.idx][1]
        if arg > self.idx + 1:
            # возможно "Иначе"
            has_elseif = False
            for j in range(self.idx + 1, arg):
                if self.ops[j][0] == 40:
                    jmp = self.ops[j][1]
                    if jmp > 0 and self.ops[jmp - 1][1] == arg:
                        has_elseif = True
                    break
            if not has_elseif:
                code = (self.stack[self.top] + "\n\n" + "Иначе") if self.top > -1 else "Иначе"
                self._push(code, 0)

    def _handle_jmp_false(self, m, last, ignore_end):
        jmp = self.ops[self.idx][1]
        if jmp <= 0 or jmp >= self.ops_count:
            return
        back = self.ops[jmp - 1][1]
        if back < self.idx:
            # цикл "Пока" — обрабатывается при опкоде 1
            return
        first_after = self.ops[back][0]
        if first_after != 1 and not self._has_jmptrue:
            # тернарная операция ?(Истина,1,2) — только в старом формате
            inner_end = back - 1
            j = self.idx + 1
            while j <= inner_end:
                opj = self.ops[j][0]
                if opj == 1 or opj == 39:
                    j += 1
                    continue
                self.idx = j
                self._process_opcode(m, last, ignore_end=True)
                # _process_opcode может продвинуть self.idx (например, LdRet пропускает
                # следующий LineNum) — учитываем это, а не сбрасываем на сохранённое значение
                j = self.idx + 1
            code = f"?({self.stack[self.top - 2]}, {self.stack[self.top - 1]}, {self.stack[self.top]})"
            self.top -= 2
            self.stack[self.top] = code
            self.idx = inner_end
        else:
            # Условие "Если"
            if back not in self.blocks:
                self.blocks[back] = "КонецЕсли;"
                code = "Если " + self.stack[self.top] + " Тогда"
            else:
                code = "ИначеЕсли " + self.stack[self.top] + " Тогда"
            self._push(code, 0)

    def _handle_for_loop(self, m):
        # цикл "Для" / "Для каждого"
        loop_range = ""
        range_idx = self.idx + 2
        while range_idx < self.ops_count:
            op = self.ops[range_idx][0]
            if op in (1, 14, 16):
                loop_range = self._pop()
                break
            saved = self.idx
            self.idx = range_idx
            self._process_opcode(m, self.ops_count - 1, ignore_end=False)
            self.idx = saved
            range_idx += 1
        if loop_range == "":
            ctx = [(j, self.ops[j][0], self.ops[j][1]) for j in range(self.idx, min(self.idx + 20, self.ops_count))]
            raise ValueError(f"не определено конечное значение в цикле (idx={self.idx}, top={self.top}, ctx={ctx})")

        # найдём конец цикла (JmpFalse → opcode 50)
        end_loop_idx = None
        for j in range(range_idx, self.ops_count):
            if self.ops[j][0] == 40 and self.ops[self.ops[j][1]][0] == 50:
                self.blocks[self.ops[j][1]] = "КонецЦикла;"
                end_loop_idx = j
                break
        if end_loop_idx is None:
            raise ValueError(f"Не найден КонецЦикла для цикла по {loop_range}")

        if self.top > -1:
            code = "Для " + self.stack[self.top] + " По " + loop_range + " Цикл"
        else:
            # "Для каждого"
            var_idx = self.ops[end_loop_idx + 1][1]
            var_name = self._local_var(m, var_idx)
            code = "Для каждого " + var_name + " Из " + loop_range + " Цикл"
            self.top = 0
        self._push(code, 0)

        # Пропускаем 6 служебных операций «Для каждого» после JmpFalse:
        #   LdLoc ЦиклПерем, LdLoc 0ЦиклПерем, Assign, LdLoc 0ЦиклПерем, LdUndef, Assign
        # (копирование элемента цикла в пользовательскую переменную + очистка временной).
        self.idx = end_loop_idx
        if end_loop_idx + 6 < self.ops_count:
            pattern = [self.ops[end_loop_idx + k][0] for k in range(1, 7)]
            if pattern == [3, 3, 16, 3, 8, 16]:
                for k in range(6):
                    self._fix_block(end_loop_idx + k)
                    self._fix_label(end_loop_idx + k)
                self.idx = end_loop_idx + 6

    # --- Сборка метода ---

    def decompile_method(self, m):
        # Внешняя декларация без тела (команда формы / функция NativeAPI):
        # только сигнатура, entry==0 и флаг «внешняя» (32). У обычного метода
        # entry==0 тоже возможно (тело начинается с опкода 0), поэтому одного
        # entry недостаточно — нужен флаг EXTERNAL.
        external_decl = (m.flags & FLAG_EXTERNAL) and m.entry == 0
        is_func = self._is_function(m) if not external_decl else bool(m.flags & FLAG_FUNCTION)
        exported = m.flags > 1 and not (m.flags & FLAG_EXTERNAL)

        args = []
        for i in range(m.argc):
            name = self._local_var(m, i)
            flags = m.vars[i][1] if i < len(m.vars) else 0
            s = name
            if i < len(m.defaults):
                kind, value = m.defaults[i]
                if kind == "S":
                    s += " = " + string_to_bsl(value)
                elif kind == "N":
                    s += " = " + str(value).replace(",", ".")
                elif kind == "B":
                    s += " = " + ("Истина" if value == "1" else "Ложь")
                elif kind == "D":
                    s += " = '" + str(value).replace("\xa0", "") + "'"
                elif kind == "L":
                    s += " = NULL"
                elif kind == "U":
                    s += " = Неопределено"
            if flags == 13:
                s = "Знач " + s
            args.append(s)

        header = ("Функция " if is_func else "Процедура ") + m.name + "(" + ", ".join(args) + ")"
        if exported:
            header += " Экспорт"

        if external_decl:
            # Внешняя декларация без тела (команда формы / функция NativeAPI /
            # глобальная встроенная функция): в байт-коде есть только сигнатура.
            # Не объявляем её как метод модуля — иначе дубликат с глобальным
            # контекстом (GetComObject, Сообщить и т.п.). Оставляем комментарий.
            return f"// Внешняя {('Функция' if is_func else 'Процедура')} {m.name}({', '.join(args)})"

        lines = [header]
        for i in range(m.argc, len(m.vars)):
            name = m.vars[i][0]
            if name.startswith("0"):
                continue
            if name.startswith("x"):
                name = f"Переменная{i}"
            lines.append(f"Перем {name};")
        lines.append("")

        body = self.decompile_method_body(m)
        lines.append(body)
        lines.append("")
        footer = ("КонецФункции" if is_func else "КонецПроцедуры") + " // " + m.name
        lines.append(footer)
        return "\n".join(lines)

    def decompile(self):
        parts = []
        for m in self.methods:
            parts.append(self.decompile_method(m))
        return "\n\n".join(parts)


def decompile_image(image):
    """Декомпилировать разобранный `image` (из bytecode.parse_image) в текст BSL."""
    return Decompiler(image).decompile()


def decompile_module(raw_dir, uuid):
    """Извлечь и декомпилировать закрытый модуль по uuid из raw-слоя каталога unpack."""
    from .bytecode import read_module_image_from_raw
    return decompile_image(read_module_image_from_raw(raw_dir, uuid))
