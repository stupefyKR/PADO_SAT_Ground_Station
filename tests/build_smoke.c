#include "gs/bootstrap.h"

int main(void)
{
    return gs_bootstrap_ready() == 1 ? 0 : 1;
}
