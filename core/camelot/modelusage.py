"""Which model ids the shipped server content already uses.

The client's monster table is 2500 entries wide (`mod.CLIENT_ID_MAX`), so a
mod may **add** a row only where the client has none; and it may **take over**
a shipped row only when nothing references it. The first half is a client
fact and is computed from the client's own tables; the second is a *server*
fact, and :data:`SHIPPED_USED_MODELS` carries the measured one.

Provenance: measured 2026-10-10 against the OpenDAoC test server — the
`npctemplate` and `mob` model columns (ranges expanded) plus every
``Model = N`` found in the server's C# sources. It is data about a server,
not about the client: a custom server may reference more, so the build takes
a replacement file (``camelot mod build --used-models``) and a server-side
loader can export its own. Ids above the client's ceiling appear too (some
server rows reference them); they can never enter the pool, so they change
nothing.

Text format: one or more tokens per line — plain numbers, ``a-b`` ranges and
``;`` separators, ``#`` comments.
"""
from __future__ import annotations

from . import gamedata

__all__ = [
    "SHIPPED_USED_MODELS",
    "client_row_ids",
    "parse_used_models",
    "pool",
    "used_model_ids",
]

SHIPPED_USED_MODELS = """\
0-14 16-20 22-92 94-120 122-180 182-408 412-418 420-422 425-426 428-430
433-505 507-509 511-515 519-528 531-532 535-619 621-622 624-644 646-653
655-682 684 686-821 823-832 834 840 842-858 860-936 938-940 942-967 969-981
983-998 1003-1008 1013 1015-1016 1018-1025 1027 1030 1032-1043 1045-1049
1056 1075 1081 1086-1087 1090 1096 1099-1101 1109 1111-1112 1120 1123-1124
1126-1128 1130-1142 1145-1146 1148 1151 1156 1158-1172 1175-1177 1179-1184
1186-1217 1219 1223-1226 1228-1229 1231-1236 1258-1263 1266-1271 1290-1292
1298 1306-1308 1313-1325 1327 1339 1343 1349-1350 1380-1386 1389-1392
1395-1398 1405-1413 1417-1421 1423-1424 1426 1429-1433 1437 1439 1489 1491
1493 1563 1583 1585 1595 1606-1607 1612-1616 1623 1625 1635 1648 1669-1670
1673 1676 1681-1682 1684 1686-1688 1690-1693 1695-1700 1702-1705 1708
1710-1712 1714-1715 1717-1721 1723-1724 1728 1730 1733-1736 1740-1750 1752
1757-1758 1770-1774 1776-1777 1779-1780 1782 1785 1789 1794 1797 1804-1807
1809 1811 1815-1864 1868 1870 1879 1883-1886 1888-1904 1908 1910-1924 1936
1938 1944-1955 1958-1963 1965-1966 1968-1990 1992-1996 1998 2000-2004
2008-2014 2016-2018 2020-2028 2032 2036 2038-2039 2041-2044 2046 2048-2049
2052 2056 2060 2064 2076-2095 2098-2099 2104-2108 2110-2111 2116 2118-2132
2134-2135 2142 2144 2146-2150 2152-2153 2156 2158 2162-2163 2165-2167
2169-2174 2176-2177 2179-2183 2186 2202 2211-2213 2218 2220-2221 2224-2228
2230-2232 2235 2238 2243 2248-2249 2251-2252 2257-2258 2262-2263 2265
2275-2276 2303-2304 2307 2309 2316 2318 2326 2328 2332 2339 2343-2347
2350-2351 2356 2358-2359 2382-2386 2388-2390 2394-2395 2494 2581-2586
2588-2592 2600-2602 2605 2646-2648 2650 2921-2934 2936-2937 2947-2952
3223-3225 3275 3359-3361 3457 3462-3469 3632 32793 32797-32798 32815
32864-32865 32873-32874 32876 32882 32884 32886 32891-32892 32894-32895
32897 32899 32904 33013 33104 33155 33160 33167 33173 33180 33209-33210
33212 33221 33225-33227 33337-33340 33342 33344-33345 33354-33355 33359
33368 33376 33383 33409 33434 33436 33438 33440 33582 33584-33585 33610
33632 33634 33660 33671 33689-33693 33696 33698-33699 33702 33704
33707-33708 33721 33735-33751 33759 33761 33765 33767-33775 33800-33802
33965 33970 34002 34026-34029 34037 34043 34444 34472 34712 34714-34718
34720 34981 34993-34994
"""


def parse_used_models(text: str) -> set[int]:
    """The ids in a used-models text (numbers, `a-b` ranges, `;`, `#`)."""
    out: set[int] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for token in line.replace(",", " ").replace(";", " ").split():
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                first, _, last = token.partition("-")
                try:
                    start, end = int(first), int(last)
                except ValueError:
                    raise ValueError(f"used-models: unreadable range {token!r}") from None
                if end < start:
                    raise ValueError(f"used-models: reversed range {token!r}")
                out.update(range(start, end + 1))
            else:
                try:
                    out.add(int(token))
                except ValueError:
                    raise ValueError(f"used-models: unreadable id {token!r}") from None
    return out


def used_model_ids(text: str | None = None) -> set[int]:
    """The shipped measured list, or a replacement text."""
    return parse_used_models(SHIPPED_USED_MODELS if text is None else text)


def client_row_ids(client_dir: str, table: str = "monsters.csv") -> list[int]:
    """The numeric ids the client's own table already carries."""
    parsed = gamedata.ClientTables(client_dir).table(table)
    return [
        int(row[0]) for row in parsed.rows[gamedata.HEADER_ROWS:]
        if row and row[0].strip().isdigit()
    ]


def pool(row_ids, used, max_id: int) -> tuple[list[int], list[int]]:
    """(free, reclaimable) model ids at or below *max_id*.

    *row_ids* are the ids the client's table already carries; *used* is the
    set of ids server content references. ``free`` ids have no row and may be
    **added**; ``reclaimable`` ids have a row nothing references and may be
    **taken over** with a `set`.
    """
    have = {int(i) for i in row_ids}
    free = [i for i in range(1, max_id + 1) if i not in have]
    reclaimable = sorted(i for i in have if i <= max_id and i not in used)
    return free, reclaimable
