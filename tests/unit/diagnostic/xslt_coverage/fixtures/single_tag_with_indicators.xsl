<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/"
                xmlns:bflc="http://id.loc.gov/ontologies/bflc/">

  <xsl:template match="marc:datafield[@tag='100']" mode="work">
    <xsl:choose>
      <xsl:when test="@ind1='0'">
        <bf:Person/>
      </xsl:when>
      <xsl:when test="@ind1='1'">
        <bf:Family/>
      </xsl:when>
    </xsl:choose>
    <xsl:for-each select="marc:subfield[@code='a']">
      <bf:contribution/>
    </xsl:for-each>
    <bflc:Contribution/>
  </xsl:template>

</xsl:stylesheet>
