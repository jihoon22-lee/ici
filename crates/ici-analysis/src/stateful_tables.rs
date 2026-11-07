// Generated CPython 3.14.7 observations; see data/python-codecs/README.txt.
const ISO_TABLES: &[IsoTable] = &[
    IsoTable {
        name: "iso2022_jp",
        shift: false,
        designations: &[
            (
                74,
                1,
                include_bytes!("../data/python-codecs/iso2022-82aa42796bf13b9b.bin"),
            ),
            (
                192,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
        ],
        g2: &[],
    },
    IsoTable {
        name: "iso2022_jp_1",
        shift: false,
        designations: &[
            (
                74,
                1,
                include_bytes!("../data/python-codecs/iso2022-82aa42796bf13b9b.bin"),
            ),
            (
                192,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                196,
                2,
                include_bytes!("../data/python-codecs/iso2022-cfa640abad6e3f84.bin"),
            ),
        ],
        g2: &[],
    },
    IsoTable {
        name: "iso2022_jp_2",
        shift: false,
        designations: &[
            (
                65,
                1,
                include_bytes!("../data/python-codecs/iso2022-5f4ecdb7b71c3e40.bin"),
            ),
            (
                70,
                1,
                include_bytes!("../data/python-codecs/iso2022-5f4ecdb7b71c3e40.bin"),
            ),
            (
                74,
                1,
                include_bytes!("../data/python-codecs/iso2022-82aa42796bf13b9b.bin"),
            ),
            (
                192,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                193,
                2,
                include_bytes!("../data/python-codecs/iso2022-4fa8377481733ef3.bin"),
            ),
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                195,
                2,
                include_bytes!("../data/python-codecs/iso2022-fad0bd5e53d84d38.bin"),
            ),
            (
                196,
                2,
                include_bytes!("../data/python-codecs/iso2022-cfa640abad6e3f84.bin"),
            ),
        ],
        g2: &[
            (
                66,
                include_bytes!("../data/python-codecs/iso2022-f6993e8ada1c11de.bin"),
            ),
            (
                65,
                include_bytes!("../data/python-codecs/iso2022-9a13d95535bef69a.bin"),
            ),
            (
                70,
                include_bytes!("../data/python-codecs/iso2022-85809019db09db91.bin"),
            ),
        ],
    },
    IsoTable {
        name: "iso2022_jp_2004",
        shift: false,
        designations: &[
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                208,
                2,
                include_bytes!("../data/python-codecs/iso2022-90b048be4cda592e.bin"),
            ),
            (
                209,
                2,
                include_bytes!("../data/python-codecs/iso2022-a69923d05c17c9e6.bin"),
            ),
        ],
        g2: &[],
    },
    IsoTable {
        name: "iso2022_jp_3",
        shift: false,
        designations: &[
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                207,
                2,
                include_bytes!("../data/python-codecs/iso2022-8bd10cf2666843d5.bin"),
            ),
            (
                208,
                2,
                include_bytes!("../data/python-codecs/iso2022-90b048be4cda592e.bin"),
            ),
        ],
        g2: &[],
    },
    IsoTable {
        name: "iso2022_jp_ext",
        shift: false,
        designations: &[
            (
                73,
                1,
                include_bytes!("../data/python-codecs/iso2022-ea597a7db537ac5f.bin"),
            ),
            (
                74,
                1,
                include_bytes!("../data/python-codecs/iso2022-82aa42796bf13b9b.bin"),
            ),
            (
                192,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                194,
                2,
                include_bytes!("../data/python-codecs/iso2022-241a84e1ca5dee1d.bin"),
            ),
            (
                196,
                2,
                include_bytes!("../data/python-codecs/iso2022-cfa640abad6e3f84.bin"),
            ),
        ],
        g2: &[],
    },
    IsoTable {
        name: "iso2022_kr",
        shift: true,
        designations: &[(
            195,
            2,
            include_bytes!("../data/python-codecs/iso2022-fad0bd5e53d84d38.bin"),
        )],
        g2: &[],
    },
];
const ISO_COMBINED: &[(u32, u32)] = &[
    (12363, 12442),
    (12365, 12442),
    (12367, 12442),
    (12369, 12442),
    (12371, 12442),
    (12459, 12442),
    (12461, 12442),
    (12463, 12442),
    (12465, 12442),
    (12467, 12442),
    (12475, 12442),
    (12484, 12442),
    (12488, 12442),
    (12791, 12442),
    (230, 768),
    (596, 768),
    (596, 769),
    (652, 768),
    (652, 769),
    (601, 768),
    (601, 769),
    (602, 768),
    (602, 769),
    (745, 741),
    (741, 745),
];
const ISO_ALIASES: &[(&str, usize)] = &[
    ("csiso2022jp", 0),
    ("csiso2022kr", 6),
    ("iso2022_jp", 0),
    ("iso2022_jp_1", 1),
    ("iso2022_jp_2", 2),
    ("iso2022_jp_2004", 3),
    ("iso2022_jp_3", 4),
    ("iso2022_jp_ext", 5),
    ("iso2022_kr", 6),
    ("iso2022jp", 0),
    ("iso2022jp_1", 1),
    ("iso2022jp_2", 2),
    ("iso2022jp_2004", 3),
    ("iso2022jp_3", 4),
    ("iso2022jp_ext", 5),
    ("iso2022kr", 6),
    ("iso_2022_jp", 0),
    ("iso_2022_jp_1", 1),
    ("iso_2022_jp_2", 2),
    ("iso_2022_jp_2004", 3),
    ("iso_2022_jp_3", 4),
    ("iso_2022_jp_ext", 5),
    ("iso_2022_kr", 6),
];
