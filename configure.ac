AC_PREREQ(2.52)

dnl Note for packagers: see pitivi/check.py for the dependencies

dnl initialize autoconf
dnl when going to/from release please set the nano (fourth number) right !
dnl releases only do Wall, cvs and prerelease does Werror too
AC_INIT(PiTiVi, 0.15.2,
    https://bugzilla.gnome.org/browse.cgi?product=pitivi,
    pitivi)

LT_INIT()

dnl initialize automake
AM_INIT_AUTOMAKE

dnl define PACKAGE_VERSION_* variables
AS_VERSION

dnl check if this is a release version
AS_NANO(PITIVI_CVS="no", PITIVI_CVS="yes")

dnl can autoconf find the source ?
AC_CONFIG_SRCDIR([pitivi/application.py])

AC_SUBST_FILE(AUTHORS)
AUTHORS=$srcdir/AUTHORS

AM_INIT_AUTOMAKE($PACKAGE, $VERSION)
AC_SUBST(ACLOCAL_AMFLAGS, "-I common/m4")

AS_AC_EXPAND(LIBDIR, $libdir)
AC_MSG_NOTICE(Storing library files in $LIBDIR)

AS_AC_EXPAND(DATADIR, $datadir)
AC_MSG_NOTICE(Storing data files in $DATADIR)

AS_AC_EXPAND(SYSCONFDIR, $sysconfdir)
AC_MSG_NOTICE(Storing configuration files in $SYSCONFDIR)

AS_AC_EXPAND(LOCALSTATEDIR, $localstatedir)
AC_MSG_NOTICE(Using localstatedir $LOCALSTATEDIR)

dnl check for python
AS_PATH_PYTHON(2.5)

dnl python checks (you can change the required python version bellow)
AM_PATH_PYTHON(2.7.0)
PY_PREFIX=`$PYTHON -c 'import sys ; print sys.prefix'`
PYTHON_LIBS="-lpython$PYTHON_VERSION"
PYTHON_CFLAGS="-I$PY_PREFIX/include/python$PYTHON_VERSION"
AC_SUBST([PYTHON_LIBS])
AC_SUBST([PYTHON_CFLAGS])

dnl ALL_LINGUAS="fr"
GETTEXT_PACKAGE="pitivi"
AC_SUBST([GETTEXT_PACKAGE])
AC_DEFINE_UNQUOTED([GETTEXT_PACKAGE], "$GETTEXT_PACKAGE", [Gettext package])
AM_GLIB_GNU_GETTEXT
IT_PROG_INTLTOOL([0.35.0])

CONFIGURED_PYTHONPATH=$PYTHONPATH
AC_SUBST(CONFIGURED_PYTHONPATH)

CONFIGURED_LD_LIBRARY_PATH=$LD_LIBRARY_PATH
AC_SUBST(CONFIGURED_LD_LIBRARY_PATH)

CONFIGURED_GST_PLUGIN_PATH=$GST_PLUGIN_PATH
AC_SUBST(CONFIGURED_GST_PLUGIN_PATH)

AC_CONFIG_FILES([bin/pitivi], [chmod +x bin/pitivi])

PKG_CHECK_MODULES([cairo], [cairo])

GNOME_DOC_INIT([0.18.0])

dnl output stuff
AC_OUTPUT(
Makefile
bin/Makefile
common/Makefile
common/m4/Makefile
help/Makefile
pitivi/Makefile
pitivi/configure.py
pitivi/dialogs/Makefile
pitivi/undo/Makefile
pitivi/utils/Makefile
pitivi/timeline/Makefile
pitivi/coptimizations/Makefile
po/Makefile.in
tests/Makefile
data/Makefile
data/pitivi.desktop.in
data/icons/Makefile
data/icons/16x16/Makefile
data/icons/22x22/Makefile
data/icons/24x24/Makefile
data/icons/32x32/Makefile
data/icons/48x48/Makefile
data/icons/256x256/Makefile
data/icons/scalable/Makefile
data/pixmaps/Makefile
data/ui/Makefile
data/renderpresets/Makefile
data/audiopresets/Makefile
data/videopresets/Makefile
)
