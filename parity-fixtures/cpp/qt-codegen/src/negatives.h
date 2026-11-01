#pragma once
// Q_OBJECT in a line comment is not a moc input.
/* Q_OBJECT in a block comment is not one either. */
static const char *quoted = "Q_OBJECT";
static const char *raw = R"qt(Q_OBJECT)qt";
static const char letter = 'Q';
#if 0
Q_OBJECT
#elif 1
#else
Q_OBJECT
#endif
#ifdef ICI_NEVER_DEFINED
#if 1
#else
Q_OBJECT
#endif
#endif
